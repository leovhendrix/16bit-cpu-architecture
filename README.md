# CPU16 - A 16-bit Multi-Cycle RISC Processor

CPU16 is a complete 16-bit processor written in Verilog, together with a visual,
animated simulator written in pure Python (standard library only, using tkinter).
The Python model follows the Verilog design cycle by cycle, so what you see in the
animation is what the hardware does on every clock edge.

## Developer

**Surafel Gashaw (Leo Vance Hendrix)**, 16 years old.

## Project files

| File            | Purpose                                                              |
|-----------------|----------------------------------------------------------------------|
| `cpu16.v`       | Verilog: ALU, register file, instruction memory, data memory, CPU    |
| `tb_cpu16.v`    | Verilog testbench with a self-check (prints PASS or FAIL)            |
| `program.hex`   | Machine code loaded into instruction memory (sum of 1 to 10)         |
| `cpu_sim.py`    | Python GUI simulator with animation, assembler and disassembler      |
| `README.md`     | This document                                                        |

## Architecture overview

- Word size: 16 bits, fixed-width 16-bit instructions
- Registers: 8 general purpose registers, `r0` to `r7`. `r0` is hard-wired to zero
  (reads as 0, writes are ignored)
- Instruction memory: 4096 words (12-bit address), read only, loaded from a hex file
- Data memory: 1024 words (10-bit address), read and write
- Architecture style: Harvard (separate instruction and data memories), load/store
- Timing: multi-cycle. Each instruction passes through up to five stages
- Flags: Zero (Z), Negative (N), Carry or borrow (C)

### Data path blocks

| Block                 | Description                                                         |
|-----------------------|---------------------------------------------------------------------|
| Program Counter (PC)  | Holds the address of the next instruction                           |
| PC + 1 adder          | Increments the PC during fetch                                      |
| Instruction Memory    | Returns the 16-bit instruction at address PC                        |
| Instruction Register  | Holds the current instruction for the rest of the instruction       |
| Register File         | 8 x 16 bits, two read ports, one write port                         |
| Reg A and Reg B       | Latch the two register file outputs during decode                   |
| ALU                   | 8 operations, produces result and Z, N, C flags                     |
| ALU Out               | Latches the ALU result (value, memory address, or branch compare)   |
| Data Memory           | Load and store storage                                              |
| MDR                   | Memory data register, holds the loaded word                         |
| Control Unit (FSM)    | Six-state finite state machine that sequences everything            |

### ALU operations

| Code | Operation | Result                          | Carry output                     |
|------|-----------|---------------------------------|----------------------------------|
| 0    | ADD       | A + B                           | carry out of bit 15              |
| 1    | SUB       | A - B                           | borrow                           |
| 2    | AND       | A and B                         | 0                                |
| 3    | OR        | A or B                          | 0                                |
| 4    | XOR       | A xor B                         | 0                                |
| 5    | SHL       | A shifted left by one           | old bit 15                       |
| 6    | SHR       | A shifted right by one (logical)| old bit 0                        |
| 7    | PASS B    | B                               | 0                                |

## Instruction set

Opcode is always bits [15:12].

| Opcode | Mnemonic | Operation                                         | Format          |
|--------|----------|---------------------------------------------------|-----------------|
| 0      | NOP      | Do nothing (`0x0000`)                             | -               |
| 0      | HALT     | Stop the CPU (`0x0001`)                           | -               |
| 1      | ADD      | `rd = rs1 + rs2`                                  | R               |
| 2      | SUB      | `rd = rs1 - rs2`                                  | R               |
| 3      | AND      | `rd = rs1 & rs2`                                  | R               |
| 4      | OR       | `rd = rs1 \| rs2`                                 | R               |
| 5      | XOR      | `rd = rs1 ^ rs2`                                  | R               |
| 6      | SHL      | `rd = rs1 << 1`                                   | R (rs2 unused)  |
| 7      | SHR      | `rd = rs1 >> 1`                                   | R (rs2 unused)  |
| 8      | ADDI     | `rd = rs1 + imm6` (signed, -32 to 31)             | I               |
| 9      | LDI      | `rd = imm8` (zero extended, 0 to 255)             | LDI             |
| 10     | LHI      | `rd = {imm8, rd[7:0]}` (sets the high byte)       | LDI             |
| 11     | LD       | `rd = mem[rs1 + imm6]`                            | I               |
| 12     | ST       | `mem[rs1 + imm6] = rt`                            | ST              |
| 13     | BEQ      | if `ra == rb` then `PC = PC + 1 + offset`         | Branch          |
| 14     | BNE      | if `ra != rb` then `PC = PC + 1 + offset`         | Branch          |
| 15     | JMP      | `PC = addr12` (absolute)                          | Jump            |

### Encodings

```
R-type   : | opcode(4) | rd(3)  | rs1(3)  | rs2(3)   | unused(3) |
I-type   : | opcode(4) | rd(3)  | rs1(3)  | imm6(6)              |
LDI/LHI  : | opcode(4) | rd(3)  | 0       | imm8(8)              |
ST       : | opcode(4) | rt(3)  | base(3) | imm6(6)              |
BEQ/BNE  : | opcode(4) | ra(3)  | rb(3)   | offset6(6)           |
JMP      : | opcode(4) | address(12)                             |
```

Building a full 16-bit constant: use `LHI` for the high byte, `LDI` for the low byte
into another register, then combine them with `OR` (see the third demo program).

## How the CPU works: the five stages

The control unit is a finite state machine. Every state lasts exactly one clock cycle.

1. **FETCH** - `IR <= instruction_memory[PC]` and `PC <= PC + 1`.
2. **DECODE** - The register file is read. `A` gets the first source register and `B`
   gets the second. For `ST`, `BEQ` and `BNE` the second source is in the `rd`
   field, and for `LHI` the first source is `rd` itself. The opcode fields are
   decoded by the control logic.
3. **EXECUTE** - The ALU works on `A` and either `B` or a sign-extended immediate.
   The result goes to `ALU Out` and the flags are updated for opcodes 1 to 8.
   For loads and stores the ALU computes the memory address. For `BEQ` and `BNE`
   the ALU subtracts and the Zero flag decides whether the PC is changed. `JMP`
   loads the PC directly.
4. **MEMORY** - Only for `LD` and `ST`. A load copies `dmem[ALU Out]` into the MDR.
   A store writes `B` into `dmem[ALU Out]`.
5. **WRITEBACK** - Only for instructions that produce a register result. The value
   (`MDR` for loads, `ALU Out` otherwise) is written to `rd`.

Cycles per instruction:

| Instruction type                         | Cycles | Path                                |
|------------------------------------------|--------|-------------------------------------|
| NOP, BEQ, BNE, JMP                       | 3      | Fetch, Decode, Execute              |
| ST                                       | 4      | Fetch, Decode, Execute, Memory      |
| ALU ops, ADDI, LDI, LHI                  | 4      | Fetch, Decode, Execute, Writeback   |
| LD                                       | 5      | Fetch, Decode, Execute, Memory, WB  |

`HALT` enters the HALT state and the processor stays there until reset.

## Running the Verilog simulation

You need any Verilog simulator. With Icarus Verilog and GTKWave:

```
iverilog -o cpu16.vvp cpu16.v tb_cpu16.v
vvp cpu16.vvp
gtkwave cpu16.vcd
```

Run the commands from the project folder so `program.hex` is found. The testbench runs
the sum program, prints a trace of every fetched instruction, dumps registers, and
prints `RESULT: PASS` when the value 55 is found in `DMEM[0]` and in `r4`.

To run a different program, assemble it (see below), export it as `program.hex`,
and run the simulation again. The module `cpu16` and the testbench use the
parameter `INIT_FILE` to select the file.

The design uses plain Verilog-2001 and is intended to be synthesizable. The memories
use initial blocks and `$readmemh`, which FPGA tools such as Vivado and Quartus support
for ROM and RAM initialization.

## Running the Python visual simulator

Requirements: Python 3 with tkinter. No third-party packages are needed.
On some Linux systems install tkinter first, for example `sudo apt install python3-tk`.

```
python3 cpu_sim.py
```

### What you see

- The center is a block diagram of the data path. When a stage is active, the blocks
  involved turn green, the buses turn yellow, and small yellow dots carry the actual
  hexadecimal values along the wires.
- The Control Unit shows the six FSM states. The current state is highlighted and
  the next state has a bright outline.
- The Register File shows all 8 registers. The registers being read or written are
  highlighted.
- On the right: the program listing (the executing instruction is highlighted), the
  first 32 words of data memory (the last written cell is highlighted), and a log
  with one line per clock cycle.
- The bottom status bar shows the cycle count, the number of executed instructions,
  and the average cycles per instruction (CPI).

### Controls

| Control           | Action                                                           |
|-------------------|------------------------------------------------------------------|
| Step Cycle        | Run one clock cycle (one FSM state) with animation               |
| Step Instruction  | Run until the current instruction is complete                    |
| Run / Pause       | Run continuously until HALT, or pause                            |
| Reset             | Reset the CPU and clear data memory                              |
| Program           | Choose one of the built-in demo programs                         |
| Edit / Assemble   | Open the assembly editor, write your own program, and load it    |
| Export HEX        | Save the current program as a `.hex` file for the Verilog design |
| Speed             | Slider that changes the animation speed                          |

### Assembly language

```
        LDI  r1, 10          ; load constant
loop:   ADD  r2, r2, r1      ; labels end with a colon
        SUB  r1, r1, r3
        BNE  r1, r0, loop    ; branch targets can be labels
        ST   r2, [r0+0]      ; memory operand: [register + offset]
        LD   r4, [r0+0]
        HALT
```

Comments start with `;` or `#`. Numbers can be decimal or hexadecimal (`0x1F`).
Immediate ranges: `ADDI` and memory offsets -32 to 31, `LDI` and `LHI` 0 to 255,
branch offsets -32 to 31 (labels are converted automatically), `JMP` 0 to 4095.

### Built-in demo programs

1. **Sum 1..10** - loops with `BNE`, stores 55 to memory, reloads it into `r4`.
2. **Fibonacci** - stores the first 10 Fibonacci numbers in data memory 0 to 9.
3. **Multiply and 16-bit constant** - computes 6 x 7 = 42 by repeated addition, builds
   `0x1234` with `LHI`, `LDI` and `OR`, then doubles it with `SHL` to get `0x2468`.

## How the Python model matches the hardware

`cpu_sim.py` contains the class `CPU16`, whose `step()` method performs exactly one
clock cycle using the same registers (`pc`, `ir`, `a`, `b`, `alu_out`, `mdr`,
flags), the same ALU equations (17-bit arithmetic for carry and borrow), the same
register-port selection rules, and the same FSM transitions as `cpu16.v`.
The GUI only draws the events that `step()` reports, so the animation is a true
view of the processor state and not a separate drawing.

## Possible future improvements

- Add a pipelined version (5 stages with hazard detection and forwarding)
- Add interrupts and a stack pointer with `CALL` and `RET`
- Add a hardware multiplier and signed comparison instructions
- Add memory-mapped input and output for FPGA boards (LEDs, switches, UART)

## License

Free to use for learning and for your own projects. Please keep the developer name:
Surafel Gashaw (Leo Vance Hendrix).
