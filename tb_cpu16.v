//==============================================================================
//  Testbench for CPU16
//  Developer : Surafel Gashaw (Leo Vance Hendrix)
//
//  Runs program.hex (sum of 1..10) until HALT and checks that the value 55
//  was stored at data memory address 0 and loaded into register r4.
//
//  Run with Icarus Verilog:
//      iverilog -o cpu16.vvp cpu16.v tb_cpu16.v
//      vvp cpu16.vvp
//      gtkwave cpu16.vcd
//==============================================================================
`timescale 1ns/1ps

module tb_cpu16;
    reg clk = 1'b0;
    reg rst = 1'b1;

    wire        halted;
    wire [15:0] dbg_pc, dbg_ir, dbg_alu_out;
    wire [2:0]  dbg_state;

    integer cycles;
    integer k;

    cpu16 #(.INIT_FILE("program.hex")) uut (
        .clk(clk),
        .rst(rst),
        .halted(halted),
        .dbg_pc(dbg_pc),
        .dbg_ir(dbg_ir),
        .dbg_alu_out(dbg_alu_out),
        .dbg_state(dbg_state)
    );

    // 100 MHz clock
    always #5 clk = ~clk;

    always @(posedge clk) begin
        if (rst) cycles <= 0;
        else     cycles <= cycles + 1;
    end

    // Trace one line per instruction fetch
    always @(posedge clk) begin
        if (!rst && dbg_state == 3'd0)
            $display("t=%0t  cycle=%0d  PC=%04h  IR=%04h", $time, cycles, dbg_pc, dbg_ir);
    end

    initial begin
        $dumpfile("cpu16.vcd");
        $dumpvars(0, tb_cpu16);

        #22 rst = 1'b0;

        wait (halted);
        #20;

        $display("------------------------------------------------");
        $display("CPU halted after %0d cycles", cycles);
        for (k = 0; k < 8; k = k + 1)
            $display("r%0d = %04h (%0d)", k, uut.u_rf.regs[k], uut.u_rf.regs[k]);
        $display("DMEM[0] = %04h (%0d)", uut.u_dmem.mem[0], uut.u_dmem.mem[0]);

        if (uut.u_dmem.mem[0] == 16'd55 && uut.u_rf.regs[4] == 16'd55)
            $display("RESULT: PASS");
        else
            $display("RESULT: FAIL");

        $finish;
    end

    // Watchdog
    initial begin
        #200000;
        $display("RESULT: FAIL (timeout)");
        $finish;
    end
endmodule
