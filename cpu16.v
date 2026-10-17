//==============================================================================
//  CPU16 - A 16-bit multi-cycle RISC processor
//  Developer : Surafel Gashaw (Leo Vance Hendrix)
//
//  Features
//    - 16-bit data path, 16-bit fixed-width instructions
//    - 8 general purpose registers (r0 is hard-wired to zero)
//    - Separate instruction memory (4096 x 16) and data memory (1024 x 16)
//    - Multi-cycle control unit: FETCH, DECODE, EXECUTE, MEMORY, WRITEBACK
//    - Flags: Zero, Negative, Carry/Borrow
//
//  Instruction formats (bit 15 is the MSB)
//    R-type : [15:12] opcode | [11:9] rd  | [8:6] rs1 | [5:3] rs2 | [2:0] unused
//    I-type : [15:12] opcode | [11:9] rd  | [8:6] rs1 | [5:0] imm6 (signed)
//    LDI/LHI: [15:12] opcode | [11:9] rd  | [8] unused | [7:0] imm8
//    ST     : [15:12] opcode | [11:9] rt  | [8:6] base | [5:0] imm6 (signed)
//    BEQ/BNE: [15:12] opcode | [11:9] ra  | [8:6] rb   | [5:0] offset (signed)
//    JMP    : [15:12] opcode | [11:0] absolute address
//    HALT   : 16'h0001 (opcode 0 with bit 0 set)
//
//  Opcode table
//    0 NOP/HALT   4 OR     8  ADDI   12 ST
//    1 ADD        5 XOR    9  LDI    13 BEQ
//    2 SUB        6 SHL    10 LHI    14 BNE
//    3 AND        7 SHR    11 LD     15 JMP
//==============================================================================
`timescale 1ns/1ps

//------------------------------------------------------------------------------
//  ALU
//------------------------------------------------------------------------------
module alu16 (
    input  wire [15:0] a,
    input  wire [15:0] b,
    input  wire [2:0]  op,
    output reg  [15:0] y,
    output wire        zero,
    output wire        neg,
    output reg         carry
);
    localparam [2:0] ALU_ADD   = 3'd0,
                     ALU_SUB   = 3'd1,
                     ALU_AND   = 3'd2,
                     ALU_OR    = 3'd3,
                     ALU_XOR   = 3'd4,
                     ALU_SHL   = 3'd5,
                     ALU_SHR   = 3'd6,
                     ALU_PASSB = 3'd7;

    reg [16:0] t;

    always @* begin
        case (op)
            ALU_ADD:   t = {1'b0, a} + {1'b0, b};
            ALU_SUB:   t = {1'b0, a} - {1'b0, b};   // t[16] = borrow
            ALU_AND:   t = {1'b0, a & b};
            ALU_OR:    t = {1'b0, a | b};
            ALU_XOR:   t = {1'b0, a ^ b};
            ALU_SHL:   t = {a, 1'b0};               // t[16] = old a[15]
            ALU_SHR:   t = {a[0], 1'b0, a[15:1]};   // t[16] = old a[0]
            default:   t = {1'b0, b};               // ALU_PASSB
        endcase
        y     = t[15:0];
        carry = t[16];
    end

    assign zero = (y == 16'd0);
    assign neg  = y[15];
endmodule

//------------------------------------------------------------------------------
//  Register file: 8 x 16, two asynchronous read ports, one synchronous write
//  port. Register 0 always reads as zero and ignores writes.
//------------------------------------------------------------------------------
module regfile16 (
    input  wire        clk,
    input  wire        rst,
    input  wire        we,
    input  wire [2:0]  raddr_a,
    input  wire [2:0]  raddr_b,
    input  wire [2:0]  waddr,
    input  wire [15:0] wdata,
    output wire [15:0] rdata_a,
    output wire [15:0] rdata_b
);
    reg [15:0] regs [0:7];
    integer i;

    assign rdata_a = (raddr_a == 3'd0) ? 16'd0 : regs[raddr_a];
    assign rdata_b = (raddr_b == 3'd0) ? 16'd0 : regs[raddr_b];

    always @(posedge clk) begin
        if (rst) begin
            for (i = 0; i < 8; i = i + 1)
                regs[i] <= 16'd0;
        end else if (we && (waddr != 3'd0)) begin
            regs[waddr] <= wdata;
        end
    end
endmodule

//------------------------------------------------------------------------------
//  Instruction memory (ROM, loaded from a hex file)
//------------------------------------------------------------------------------
module imem16 #(
    parameter INIT_FILE = "program.hex"
) (
    input  wire [11:0] addr,
    output wire [15:0] data
);
    reg [15:0] mem [0:4095];
    integer i;

    initial begin
        for (i = 0; i < 4096; i = i + 1)
            mem[i] = 16'h0000;
        $readmemh(INIT_FILE, mem);
    end

    assign data = mem[addr];
endmodule

//------------------------------------------------------------------------------
//  Data memory (RAM): asynchronous read, synchronous write
//------------------------------------------------------------------------------
module dmem16 (
    input  wire        clk,
    input  wire        we,
    input  wire [9:0]  addr,
    input  wire [15:0] wdata,
    output wire [15:0] rdata
);
    reg [15:0] mem [0:1023];
    integer i;

    initial begin
        for (i = 0; i < 1024; i = i + 1)
            mem[i] = 16'h0000;
    end

    always @(posedge clk) begin
        if (we)
            mem[addr] <= wdata;
    end

    assign rdata = mem[addr];
endmodule

//------------------------------------------------------------------------------
//  CPU top level: data path + multi-cycle control FSM
//------------------------------------------------------------------------------
module cpu16 #(
    parameter INIT_FILE = "program.hex"
) (
    input  wire        clk,
    input  wire        rst,
    output wire        halted,
    output wire [15:0] dbg_pc,
    output wire [15:0] dbg_ir,
    output wire [15:0] dbg_alu_out,
    output wire [2:0]  dbg_state
);
    // Opcodes
    localparam [3:0] OP_NOP = 4'd0,  OP_ADD = 4'd1,  OP_SUB = 4'd2,  OP_AND = 4'd3,
                     OP_OR  = 4'd4,  OP_XOR = 4'd5,  OP_SHL = 4'd6,  OP_SHR = 4'd7,
                     OP_ADDI = 4'd8, OP_LDI = 4'd9,  OP_LHI = 4'd10, OP_LD  = 4'd11,
                     OP_ST  = 4'd12, OP_BEQ = 4'd13, OP_BNE = 4'd14, OP_JMP = 4'd15;

    // ALU operations
    localparam [2:0] ALU_ADD = 3'd0, ALU_SUB = 3'd1, ALU_AND = 3'd2, ALU_OR = 3'd3,
                     ALU_XOR = 3'd4, ALU_SHL = 3'd5, ALU_SHR = 3'd6, ALU_PASSB = 3'd7;

    // FSM states
    localparam [2:0] S_FETCH = 3'd0, S_DECODE = 3'd1, S_EXEC = 3'd2,
                     S_MEM = 3'd3,   S_WB = 3'd4,     S_HALT = 3'd5;

    // Architectural / micro-architectural registers
    reg [15:0] pc, ir, a_reg, b_reg, alu_out, mdr;
    reg        flag_z, flag_n, flag_c;
    reg [2:0]  state;

    // Instruction field decode
    wire [3:0]  opcode  = ir[15:12];
    wire [2:0]  rd      = ir[11:9];
    wire [2:0]  rs1     = ir[8:6];
    wire [2:0]  rs2     = ir[5:3];
    wire [15:0] imm6    = {{10{ir[5]}}, ir[5:0]};
    wire [15:0] imm8    = {8'h00, ir[7:0]};
    wire [11:0] jaddr   = ir[11:0];
    wire        is_halt = (opcode == OP_NOP) && ir[0];

    // Register file read-port selection
    //   ST, BEQ, BNE keep their second source register in the rd field.
    //   LHI reads its own destination register (to keep the low byte).
    wire src_in_rd = (opcode == OP_ST) || (opcode == OP_BEQ) || (opcode == OP_BNE);
    wire [2:0] ra_sel = (opcode == OP_LHI) ? rd : rs1;
    wire [2:0] rb_sel = src_in_rd ? rd : rs2;

    wire [15:0] rf_a, rf_b;
    wire        rf_we    = (state == S_WB);
    wire [15:0] rf_wdata = (opcode == OP_LD) ? mdr : alu_out;

    regfile16 u_rf (
        .clk(clk), .rst(rst), .we(rf_we),
        .raddr_a(ra_sel), .raddr_b(rb_sel),
        .waddr(rd), .wdata(rf_wdata),
        .rdata_a(rf_a), .rdata_b(rf_b)
    );

    // ALU control
    reg [2:0] alu_op;
    always @* begin
        case (opcode)
            OP_ADD:          alu_op = ALU_ADD;
            OP_SUB:          alu_op = ALU_SUB;
            OP_AND:          alu_op = ALU_AND;
            OP_OR:           alu_op = ALU_OR;
            OP_XOR:          alu_op = ALU_XOR;
            OP_SHL:          alu_op = ALU_SHL;
            OP_SHR:          alu_op = ALU_SHR;
            OP_LDI:          alu_op = ALU_PASSB;
            OP_BEQ, OP_BNE:  alu_op = ALU_SUB;
            default:         alu_op = ALU_ADD;   // ADDI, LD, ST address calc
        endcase
    end

    // ALU operand B multiplexer
    reg [15:0] alu_b;
    always @* begin
        case (opcode)
            OP_ADDI, OP_LD, OP_ST: alu_b = imm6;
            OP_LDI:                alu_b = imm8;
            default:               alu_b = b_reg;
        endcase
    end

    wire [15:0] alu_y;
    wire        alu_zero, alu_neg, alu_carry;

    alu16 u_alu (
        .a(a_reg), .b(alu_b), .op(alu_op),
        .y(alu_y), .zero(alu_zero), .neg(alu_neg), .carry(alu_carry)
    );

    // Memories
    wire [15:0] imem_data;
    wire [15:0] dmem_rdata;
    wire        dmem_we = (state == S_MEM) && (opcode == OP_ST);

    imem16 #(.INIT_FILE(INIT_FILE)) u_imem (
        .addr(pc[11:0]), .data(imem_data)
    );

    dmem16 u_dmem (
        .clk(clk), .we(dmem_we), .addr(alu_out[9:0]),
        .wdata(b_reg), .rdata(dmem_rdata)
    );

    // Control FSM and data path registers
    always @(posedge clk) begin
        if (rst) begin
            pc      <= 16'd0;
            ir      <= 16'd0;
            a_reg   <= 16'd0;
            b_reg   <= 16'd0;
            alu_out <= 16'd0;
            mdr     <= 16'd0;
            flag_z  <= 1'b0;
            flag_n  <= 1'b0;
            flag_c  <= 1'b0;
            state   <= S_FETCH;
        end else begin
            case (state)
                //----------------------------------------------------------
                S_FETCH: begin
                    ir    <= imem_data;
                    pc    <= pc + 16'd1;
                    state <= S_DECODE;
                end
                //----------------------------------------------------------
                S_DECODE: begin
                    a_reg <= rf_a;
                    b_reg <= rf_b;
                    state <= S_EXEC;
                end
                //----------------------------------------------------------
                S_EXEC: begin
                    alu_out <= (opcode == OP_LHI) ? {ir[7:0], a_reg[7:0]} : alu_y;

                    if (opcode >= OP_ADD && opcode <= OP_ADDI) begin
                        flag_z <= alu_zero;
                        flag_n <= alu_neg;
                        flag_c <= alu_carry;
                    end

                    case (opcode)
                        OP_NOP: state <= is_halt ? S_HALT : S_FETCH;

                        OP_ADD, OP_SUB, OP_AND, OP_OR, OP_XOR,
                        OP_SHL, OP_SHR, OP_ADDI, OP_LDI, OP_LHI:
                            state <= S_WB;

                        OP_LD, OP_ST:
                            state <= S_MEM;

                        OP_BEQ: begin
                            if (alu_zero)  pc <= pc + imm6;
                            state <= S_FETCH;
                        end

                        OP_BNE: begin
                            if (!alu_zero) pc <= pc + imm6;
                            state <= S_FETCH;
                        end

                        OP_JMP: begin
                            pc    <= {4'b0000, jaddr};
                            state <= S_FETCH;
                        end

                        default: state <= S_FETCH;
                    endcase
                end
                //----------------------------------------------------------
                S_MEM: begin
                    if (opcode == OP_LD) begin
                        mdr   <= dmem_rdata;
                        state <= S_WB;
                    end else begin
                        state <= S_FETCH;       // store is written by dmem16
                    end
                end
                //----------------------------------------------------------
                S_WB: begin
                    state <= S_FETCH;           // register write happens now
                end
                //----------------------------------------------------------
                S_HALT: begin
                    state <= S_HALT;
                end
                default: state <= S_FETCH;
            endcase
        end
    end

    assign halted      = (state == S_HALT);
    assign dbg_pc      = pc;
    assign dbg_ir      = ir;
    assign dbg_alu_out = alu_out;
    assign dbg_state   = state;
endmodule
