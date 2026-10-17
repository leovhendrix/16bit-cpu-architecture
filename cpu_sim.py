#!/usr/bin/env python3
"""
CPU16 Visual Simulator
Developer : Surafel Gashaw (Leo Vance Hendrix)

A cycle-accurate Python model of the Verilog design in cpu16.v with an
animated block diagram. Only the Python standard library is used
(tkinter for the graphical interface).

Run:
    python3 cpu_sim.py
"""

import math
import re
import time
import tkinter as tk
from tkinter import filedialog, messagebox

# ----------------------------------------------------------------------------
#  Architecture constants (must match cpu16.v)
# ----------------------------------------------------------------------------
(OP_NOP, OP_ADD, OP_SUB, OP_AND, OP_OR, OP_XOR, OP_SHL, OP_SHR,
 OP_ADDI, OP_LDI, OP_LHI, OP_LD, OP_ST, OP_BEQ, OP_BNE, OP_JMP) = range(16)

S_FETCH, S_DECODE, S_EXEC, S_MEM, S_WB, S_HALT = range(6)
STATE_NAMES = ["FETCH", "DECODE", "EXECUTE", "MEMORY", "WRITEBACK", "HALT"]

ALU_NAMES = ["ADD", "SUB", "AND", "OR", "XOR", "SHL", "SHR", "PASS B"]

MASK = 0xFFFF
IMEM_SIZE = 4096
DMEM_SIZE = 1024

MNEMONICS = {
    "NOP": OP_NOP, "HALT": OP_NOP, "ADD": OP_ADD, "SUB": OP_SUB,
    "AND": OP_AND, "OR": OP_OR, "XOR": OP_XOR, "SHL": OP_SHL,
    "SHR": OP_SHR, "ADDI": OP_ADDI, "LDI": OP_LDI, "LHI": OP_LHI,
    "LD": OP_LD, "ST": OP_ST, "BEQ": OP_BEQ, "BNE": OP_BNE, "JMP": OP_JMP,
}
OP_NAME = {v: k for k, v in MNEMONICS.items() if k != "HALT"}


def sext(value, bits):
    """Sign-extend an integer of the given width."""
    value &= (1 << bits) - 1
    if value & (1 << (bits - 1)):
        value -= 1 << bits
    return value


# ----------------------------------------------------------------------------
#  ALU model (mirrors alu16 in cpu16.v)
# ----------------------------------------------------------------------------
def alu(op, a, b):
    if op == 0:
        t = (a + b) & 0x1FFFF
    elif op == 1:
        t = (a - b) & 0x1FFFF
    elif op == 2:
        t = a & b
    elif op == 3:
        t = a | b
    elif op == 4:
        t = a ^ b
    elif op == 5:
        t = (a << 1) & 0x1FFFF
    elif op == 6:
        t = ((a & 1) << 16) | (a >> 1)
    else:
        t = b
    y = t & MASK
    carry = (t >> 16) & 1
    zero = 1 if y == 0 else 0
    neg = (y >> 15) & 1
    return y, zero, neg, carry


def alu_op_for(opcode):
    return {
        OP_ADD: 0, OP_SUB: 1, OP_AND: 2, OP_OR: 3, OP_XOR: 4,
        OP_SHL: 5, OP_SHR: 6, OP_LDI: 7, OP_BEQ: 1, OP_BNE: 1,
    }.get(opcode, 0)


# ----------------------------------------------------------------------------
#  Disassembler
# ----------------------------------------------------------------------------
def disasm(w, addr=None):
    op = w >> 12
    rd = (w >> 9) & 7
    rs1 = (w >> 6) & 7
    rs2 = (w >> 3) & 7
    imm6 = sext(w, 6)
    if op == OP_NOP:
        return "HALT" if (w & 1) else "NOP"
    if op in (OP_ADD, OP_SUB, OP_AND, OP_OR, OP_XOR):
        return "%s r%d, r%d, r%d" % (OP_NAME[op], rd, rs1, rs2)
    if op in (OP_SHL, OP_SHR):
        return "%s r%d, r%d" % (OP_NAME[op], rd, rs1)
    if op == OP_ADDI:
        return "ADDI r%d, r%d, %d" % (rd, rs1, imm6)
    if op in (OP_LDI, OP_LHI):
        return "%s r%d, 0x%02X" % (OP_NAME[op], rd, w & 0xFF)
    if op == OP_LD:
        return "LD r%d, [r%d%+d]" % (rd, rs1, imm6)
    if op == OP_ST:
        return "ST r%d, [r%d%+d]" % (rd, rs1, imm6)
    if op in (OP_BEQ, OP_BNE):
        if addr is not None:
            tgt = "0x%03X" % ((addr + 1 + imm6) & 0xFFF)
        else:
            tgt = "%+d" % imm6
        return "%s r%d, r%d, %s" % (OP_NAME[op], rd, rs1, tgt)
    return "JMP 0x%03X" % (w & 0xFFF)


# ----------------------------------------------------------------------------
#  Assembler
# ----------------------------------------------------------------------------
class AsmError(Exception):
    pass


_REG_RE = re.compile(r"^[rR]([0-7])$")
_MEM_RE = re.compile(r"^\[\s*([rR][0-7])\s*(?:([+-])\s*([0-9xXa-fA-F]+))?\s*\]$")
_LABEL_RE = re.compile(r"^[A-Za-z_]\w*$")


def _reg(tok, ln):
    m = _REG_RE.match(tok.strip())
    if not m:
        raise AsmError("Line %d: bad register '%s'" % (ln, tok))
    return int(m.group(1))


def _int(tok, ln):
    try:
        return int(tok.strip(), 0)
    except ValueError:
        raise AsmError("Line %d: bad number '%s'" % (ln, tok))


def _mem(tok, ln):
    m = _MEM_RE.match(tok.strip())
    if not m:
        raise AsmError("Line %d: bad memory operand '%s'" % (ln, tok))
    reg = _reg(m.group(1), ln)
    imm = 0
    if m.group(2):
        imm = _int(m.group(3), ln)
        if m.group(2) == "-":
            imm = -imm
    return reg, imm


def _check(value, lo, hi, what, ln):
    if not (lo <= value <= hi):
        raise AsmError("Line %d: %s %d out of range (%d..%d)" % (ln, what, value, lo, hi))
    return value


def assemble(text):
    """Return a list of 16-bit instruction words."""
    items = []     # (line_no, mnemonic, operands, address)
    labels = {}
    addr = 0
    for ln, raw in enumerate(text.splitlines(), 1):
        line = re.split(r"[;#]", raw)[0].strip()
        if not line:
            continue
        if ":" in line:
            name, line = line.split(":", 1)
            name = name.strip()
            if not _LABEL_RE.match(name):
                raise AsmError("Line %d: bad label '%s'" % (ln, name))
            if name in labels:
                raise AsmError("Line %d: duplicate label '%s'" % (ln, name))
            labels[name] = addr
            line = line.strip()
            if not line:
                continue
        parts = line.split(None, 1)
        mnem = parts[0].upper()
        ops = [o.strip() for o in parts[1].split(",")] if len(parts) > 1 else []
        if mnem not in MNEMONICS:
            raise AsmError("Line %d: unknown instruction '%s'" % (ln, parts[0]))
        items.append((ln, mnem, ops, addr))
        addr += 1
    if addr > IMEM_SIZE:
        raise AsmError("Program too large (%d words, max %d)" % (addr, IMEM_SIZE))

    words = []
    for ln, mnem, ops, addr in items:
        opc = MNEMONICS[mnem]

        def need(n):
            if len(ops) != n:
                raise AsmError("Line %d: %s expects %d operand(s)" % (ln, mnem, n))

        if mnem == "NOP":
            need(0)
            w = 0x0000
        elif mnem == "HALT":
            need(0)
            w = 0x0001
        elif opc in (OP_ADD, OP_SUB, OP_AND, OP_OR, OP_XOR):
            need(3)
            w = (opc << 12) | (_reg(ops[0], ln) << 9) | (_reg(ops[1], ln) << 6) | (_reg(ops[2], ln) << 3)
        elif opc in (OP_SHL, OP_SHR):
            need(2)
            w = (opc << 12) | (_reg(ops[0], ln) << 9) | (_reg(ops[1], ln) << 6)
        elif opc == OP_ADDI:
            need(3)
            imm = _check(_int(ops[2], ln), -32, 31, "immediate", ln)
            w = (opc << 12) | (_reg(ops[0], ln) << 9) | (_reg(ops[1], ln) << 6) | (imm & 0x3F)
        elif opc in (OP_LDI, OP_LHI):
            need(2)
            imm = _check(_int(ops[1], ln), 0, 255, "immediate", ln)
            w = (opc << 12) | (_reg(ops[0], ln) << 9) | imm
        elif opc in (OP_LD, OP_ST):
            need(2)
            base, imm = _mem(ops[1], ln)
            _check(imm, -32, 31, "offset", ln)
            w = (opc << 12) | (_reg(ops[0], ln) << 9) | (base << 6) | (imm & 0x3F)
        elif opc in (OP_BEQ, OP_BNE):
            need(3)
            if ops[2] in labels:
                off = labels[ops[2]] - (addr + 1)
            elif _LABEL_RE.match(ops[2]) and not _REG_RE.match(ops[2]):
                raise AsmError("Line %d: unknown label '%s'" % (ln, ops[2]))
            else:
                off = _int(ops[2], ln)
            _check(off, -32, 31, "branch offset", ln)
            w = (opc << 12) | (_reg(ops[0], ln) << 9) | (_reg(ops[1], ln) << 6) | (off & 0x3F)
        else:  # JMP
            need(1)
            if ops[0] in labels:
                target = labels[ops[0]]
            elif _LABEL_RE.match(ops[0]):
                raise AsmError("Line %d: unknown label '%s'" % (ln, ops[0]))
            else:
                target = _int(ops[0], ln)
            _check(target, 0, 4095, "jump address", ln)
            w = (opc << 12) | target
        words.append(w & MASK)
    return words


# ----------------------------------------------------------------------------
#  CPU model (cycle-accurate, mirrors cpu16.v)
# ----------------------------------------------------------------------------
class CPU16:
    def __init__(self):
        self.imem = [0] * IMEM_SIZE
        self.prog_len = 0
        self.reset()

    def load(self, words):
        self.imem = [0] * IMEM_SIZE
        for i, w in enumerate(words):
            self.imem[i] = w & MASK
        self.prog_len = len(words)
        self.reset()

    def reset(self):
        self.pc = 0
        self.ir = 0
        self.ir_addr = 0
        self.a = 0
        self.b = 0
        self.alu_out = 0
        self.mdr = 0
        self.z = self.n = self.c = 0
        self.state = S_FETCH
        self.regs = [0] * 8
        self.dmem = [0] * DMEM_SIZE
        self.cycles = 0
        self.instructions = 0
        self.last_alu_op = 0
        self.last_mem = None   # (address, value, "R" or "W")

    # One clock cycle. Returns a dict describing what happened for the GUI.
    def step(self):
        s = self.state
        ir = self.ir
        opc = ir >> 12
        rd = (ir >> 9) & 7
        rs1 = (ir >> 6) & 7
        rs2 = (ir >> 3) & 7
        imm6 = sext(ir, 6)
        imm8 = ir & 0xFF
        info = {"state": s, "active": ["CTRL"], "transfers": [], "rows": [], "msg": ""}
        T = info["transfers"]
        act = info["active"]

        if s == S_FETCH:
            old = self.pc
            self.ir = self.imem[old & 0xFFF]
            self.ir_addr = old
            self.pc = (old + 1) & MASK
            act += ["PC", "IMEM", "IR", "ADD1"]
            T += [("pc_imem", "%04X" % old, 0.00, 0.35),
                  ("imem_ir", "%04X" % self.ir, 0.35, 0.80),
                  ("pc_add", "%04X" % old, 0.00, 0.30),
                  ("add_pc", "%04X" % self.pc, 0.30, 0.80)]
            info["msg"] = "IR <= mem[%03X] = %04X (%s), PC <= %04X" % (
                old & 0xFFF, self.ir, disasm(self.ir, old), self.pc)
            self.state = S_DECODE

        elif s == S_DECODE:
            src_in_rd = opc in (OP_ST, OP_BEQ, OP_BNE)
            ra = rd if opc == OP_LHI else rs1
            rb = rd if src_in_rd else rs2
            self.a = self.regs[ra]
            self.b = self.regs[rb]
            act += ["IR", "RF", "A", "B"]
            info["rows"] = sorted(set([ra, rb]))
            T += [("ir_rf", "r%d,r%d" % (ra, rb), 0.00, 0.35),
                  ("rf_a", "%04X" % self.a, 0.35, 0.85),
                  ("rf_b", "%04X" % self.b, 0.35, 0.85)]
            info["msg"] = "A <= r%d = %04X, B <= r%d = %04X" % (ra, self.a, rb, self.b)
            self.state = S_EXEC

        elif s == S_EXEC:
            aluop = alu_op_for(opc)
            uses_a = opc not in (OP_NOP, OP_LDI, OP_JMP)
            uses_b = opc in (OP_ADD, OP_SUB, OP_AND, OP_OR, OP_XOR, OP_BEQ, OP_BNE)
            uses_imm = opc in (OP_ADDI, OP_LD, OP_ST, OP_LDI)
            if opc in (OP_ADDI, OP_LD, OP_ST):
                bval = imm6 & MASK
            elif opc == OP_LDI:
                bval = imm8
            else:
                bval = self.b
            y, z, n, c = alu(aluop, self.a, bval)
            self.last_alu_op = aluop
            if opc == OP_LHI:
                self.alu_out = ((ir & 0xFF) << 8) | (self.a & 0xFF)
            else:
                self.alu_out = y
            flags_upd = OP_ADD <= opc <= OP_ADDI
            if flags_upd:
                self.z, self.n, self.c = z, n, c

            nxt = S_FETCH
            msg = ""
            if opc not in (OP_NOP, OP_JMP):
                act += ["ALU", "ALUOUT"]
                if uses_a:
                    act.append("A")
                    T.append(("a_alu", "%04X" % self.a, 0.00, 0.30))
                if uses_b:
                    act.append("B")
                    T.append(("b_alu", "%04X" % self.b, 0.00, 0.30))
                if uses_imm:
                    act.append("IR")
                    T.append(("ir_alu", "%04X" % bval, 0.00, 0.30))
                T.append(("alu_out", "%04X" % self.alu_out, 0.30, 0.65))
                if flags_upd:
                    act.append("FLAGS")
                    T.append(("alu_flags", "Z%d N%d C%d" % (z, n, c), 0.30, 0.65))
                msg = "ALU %s(%04X, %04X) = %04X" % (ALU_NAMES[aluop], self.a, bval, y)
                if opc == OP_LHI:
                    msg = "LHI: ALUOUT <= {%02X, %02X} = %04X" % (ir & 0xFF, self.a & 0xFF, self.alu_out)

            if opc == OP_NOP:
                if ir & 1:
                    nxt = S_HALT
                    msg = "HALT: processor stopped"
                else:
                    msg = "NOP"
            elif opc in (OP_ADD, OP_SUB, OP_AND, OP_OR, OP_XOR, OP_SHL, OP_SHR,
                         OP_ADDI, OP_LDI, OP_LHI):
                nxt = S_WB
            elif opc in (OP_LD, OP_ST):
                nxt = S_MEM
                msg = "address = %04X" % self.alu_out
            elif opc in (OP_BEQ, OP_BNE):
                taken = bool(z) if opc == OP_BEQ else not z
                if taken:
                    self.pc = (self.pc + imm6) & MASK
                    act.append("PC")
                    T.append(("ir_pc", "%04X" % self.pc, 0.65, 0.95))
                    msg += ", branch taken, PC <= %04X" % self.pc
                else:
                    msg += ", branch not taken"
            elif opc == OP_JMP:
                self.pc = ir & 0xFFF
                act += ["IR", "PC"]
                T.append(("ir_pc", "%04X" % self.pc, 0.10, 0.80))
                msg = "JMP: PC <= %04X" % self.pc
            info["msg"] = msg
            self.state = nxt

        elif s == S_MEM:
            addr = self.alu_out & (DMEM_SIZE - 1)
            act += ["ALUOUT", "DMEM"]
            if opc == OP_LD:
                self.mdr = self.dmem[addr]
                self.last_mem = (addr, self.mdr, "R")
                act.append("MDR")
                T += [("alu_dmem", "%03X" % addr, 0.00, 0.40),
                      ("dmem_mdr", "%04X" % self.mdr, 0.40, 0.90)]
                info["msg"] = "MDR <= dmem[%03X] = %04X" % (addr, self.mdr)
                self.state = S_WB
            else:
                self.dmem[addr] = self.b
                self.last_mem = (addr, self.b, "W")
                act.append("B")
                T += [("alu_dmem", "%03X" % addr, 0.00, 0.50),
                      ("b_dmem", "%04X" % self.b, 0.00, 0.90)]
                info["msg"] = "dmem[%03X] <= %04X" % (addr, self.b)
                self.state = S_FETCH

        elif s == S_WB:
            if opc == OP_LD:
                val = self.mdr
                T.append(("mdr_rf", "%04X" % val, 0.00, 0.90))
                act.append("MDR")
            else:
                val = self.alu_out
                T.append(("alu_rf", "%04X" % val, 0.00, 0.90))
                act.append("ALUOUT")
            act.append("RF")
            if rd != 0:
                self.regs[rd] = val
                info["rows"] = [rd]
                info["msg"] = "r%d <= %04X" % (rd, val)
            else:
                info["msg"] = "write to r0 ignored"
            self.state = S_FETCH

        else:
            info["msg"] = "halted"

        if s != S_HALT and self.state in (S_FETCH, S_HALT):
            self.instructions += 1
        self.cycles += 1
        return info


# ----------------------------------------------------------------------------
#  Demo programs
# ----------------------------------------------------------------------------
DEMO_SUM = """\
; Sum of 1..10. Result 55 is stored at data memory address 0.
        LDI  r1, 10          ; counter
        LDI  r2, 0           ; sum
        LDI  r3, 1           ; constant 1
loop:   ADD  r2, r2, r1
        SUB  r1, r1, r3
        BNE  r1, r0, loop
        ST   r2, [r0+0]
        LD   r4, [r0+0]
        HALT
"""

DEMO_FIB = """\
; First 10 Fibonacci numbers stored at data memory addresses 0..9.
        LDI  r1, 0           ; a
        LDI  r2, 1           ; b
        LDI  r3, 0           ; memory index
        LDI  r4, 10          ; loop counter
        LDI  r5, 1           ; constant 1
loop:   ST   r1, [r3+0]
        ADD  r6, r1, r2      ; next = a + b
        ADD  r1, r2, r0      ; a = b
        ADD  r2, r6, r0      ; b = next
        ADDI r3, r3, 1
        SUB  r4, r4, r5
        BNE  r4, r0, loop
        HALT
"""

DEMO_MUL = """\
; 6 * 7 by repeated addition, then build the 16-bit constant 0x1234
; with LHI + OR and double it with SHL.
        LDI  r1, 7
        LDI  r2, 6
        LDI  r3, 0
        LDI  r4, 1
loop:   ADD  r3, r3, r1
        SUB  r2, r2, r4
        BNE  r2, r0, loop
        LHI  r5, 0x12        ; r5 = 0x1200
        LDI  r6, 0x34
        OR   r5, r5, r6      ; r5 = 0x1234
        SHL  r7, r5          ; r7 = 0x2468
        ST   r3, [r0+0]      ; 42
        ST   r5, [r0+1]      ; 0x1234
        ST   r7, [r0+2]      ; 0x2468
        HALT
"""

DEMOS = {
    "Sum 1..10": DEMO_SUM,
    "Fibonacci": DEMO_FIB,
    "Multiply and 16-bit constant": DEMO_MUL,
}

# ----------------------------------------------------------------------------
#  GUI
# ----------------------------------------------------------------------------
BG = "#10151c"
PANEL = "#18202a"
BLOCK = "#1f2933"
BLOCK2 = "#27323f"
OUTLINE = "#52627a"
TITLE = "#9fb3c8"
VALUE = "#e6edf5"
ACT_FILL = "#1f6f50"
ACT_OUT = "#3ddc97"
ROW_ACT = "#8a6d1c"
WIRE = "#3b4859"
WIRE_ACT = "#f0b429"
PKT = "#ffd24a"
PKT_TEXT = "#fff0b3"
BTN = "#2b3a4d"
BTN_FG = "#e6edf5"

F_TITLE = ("Helvetica", 9, "bold")
F_VAL = ("Courier", 11, "bold")
F_SMALL = ("Courier", 9, "bold")
F_UI = ("Helvetica", 10)
F_MONO = ("Courier", 9)

BLOCKS = {
    "PC":     (30, 60, 130, 110, "Program Counter"),
    "ADD1":   (30, 150, 130, 190, "PC + 1"),
    "IMEM":   (200, 40, 350, 130, "Instruction Memory"),
    "IR":     (410, 55, 550, 115, "Instruction Register"),
    "CTRL":   (620, 30, 860, 130, "Control Unit (FSM)"),
    "RF":     (200, 190, 350, 380, "Register File"),
    "A":      (420, 215, 500, 250, "Reg A"),
    "B":      (420, 310, 500, 345, "Reg B"),
    "ALU":    (580, 215, 700, 345, "ALU"),
    "ALUOUT": (760, 250, 860, 300, "ALU Out"),
    "FLAGS":  (580, 380, 700, 410, "Flags"),
    "DMEM":   (720, 400, 900, 490, "Data Memory"),
    "MDR":    (560, 440, 650, 480, "MDR"),
}

WIRES = {
    "pc_imem":   [(130, 85), (200, 85)],
    "imem_ir":   [(350, 85), (410, 85)],
    "pc_add":    [(80, 110), (80, 150)],
    "add_pc":    [(30, 170), (14, 170), (14, 85), (30, 85)],
    "ir_rf":     [(440, 115), (440, 135), (370, 135), (370, 215), (350, 215)],
    "rf_a":      [(350, 232), (420, 232)],
    "rf_b":      [(350, 327), (420, 327)],
    "a_alu":     [(500, 232), (580, 232)],
    "b_alu":     [(500, 327), (580, 327)],
    "ir_alu":    [(500, 115), (500, 170), (640, 170), (640, 215)],
    "alu_out":   [(700, 275), (760, 275)],
    "alu_flags": [(640, 345), (640, 380)],
    "alu_dmem":  [(810, 300), (810, 400)],
    "b_dmem":    [(460, 345), (460, 520), (760, 520), (760, 490)],
    "dmem_mdr":  [(720, 460), (650, 460)],
    "mdr_rf":    [(560, 460), (275, 460), (275, 380)],
    "alu_rf":    [(810, 250), (810, 150), (395, 150), (395, 270), (350, 270)],
    "ir_pc":     [(480, 55), (480, 20), (80, 20), (80, 60)],
}


def path_geometry(pts):
    segs = []
    total = 0.0
    for i in range(len(pts) - 1):
        d = math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
        segs.append(d)
        total += d
    return pts, segs, total


def point_on_path(pts, segs, total, p):
    target = max(0.0, min(1.0, p)) * total
    for i, d in enumerate(segs):
        if target <= d or i == len(segs) - 1:
            f = 0.0 if d == 0 else min(1.0, target / d)
            x = pts[i][0] + (pts[i + 1][0] - pts[i][0]) * f
            y = pts[i][1] + (pts[i + 1][1] - pts[i][1]) * f
            return x, y
        target -= d
    return pts[-1]


class App:
    def __init__(self, root):
        self.root = root
        root.title("CPU16 Visual Simulator - Surafel Gashaw (Leo Vance Hendrix)")
        root.configure(bg=BG)
        self.cpu = CPU16()
        self.mode = "idle"        # idle, cycle, instr, run
        self.anim = None
        self.source = DEMO_SUM
        self.editor = None
        self.rects = {}
        self.vals = {}
        self.wires = {}
        self.wire_geo = {}
        self.row_rects = []
        self.row_vals = []
        self.state_rects = []
        self.build_ui()
        self.load_source(self.source, quiet=True)

    # ---------------------------------------------------------------- UI build
    def mkbtn(self, parent, text, cmd):
        b = tk.Button(parent, text=text, command=cmd, bg=BTN, fg=BTN_FG,
                      activebackground="#3a4d66", activeforeground=BTN_FG,
                      relief="flat", padx=10, pady=4, font=F_UI)
        b.pack(side="left", padx=3, pady=6)
        return b

    def build_ui(self):
        bar = tk.Frame(self.root, bg=PANEL)
        bar.pack(fill="x")
        self.mkbtn(bar, "Step Cycle", self.on_step_cycle)
        self.mkbtn(bar, "Step Instruction", self.on_step_instr)
        self.btn_run = self.mkbtn(bar, "Run", self.on_run)
        self.mkbtn(bar, "Reset", self.on_reset)

        tk.Label(bar, text="  Program:", bg=PANEL, fg=TITLE, font=F_UI).pack(side="left")
        self.prog_var = tk.StringVar(value=list(DEMOS)[0])
        om = tk.OptionMenu(bar, self.prog_var, *DEMOS.keys(), command=self.on_demo)
        om.config(bg=BTN, fg=BTN_FG, activebackground="#3a4d66", activeforeground=BTN_FG,
                  relief="flat", highlightthickness=0, font=F_UI)
        om["menu"].config(bg=BTN, fg=BTN_FG, font=F_UI)
        om.pack(side="left", padx=3)

        self.mkbtn(bar, "Edit / Assemble", self.open_editor)
        self.mkbtn(bar, "Export HEX", self.export_hex)

        tk.Label(bar, text="  Speed:", bg=PANEL, fg=TITLE, font=F_UI).pack(side="left")
        self.speed = tk.Scale(bar, from_=1, to=20, orient="horizontal", length=140,
                              bg=PANEL, fg=TITLE, troughcolor=BLOCK2,
                              highlightthickness=0, showvalue=False)
        self.speed.set(5)
        self.speed.pack(side="left", padx=4)

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True)

        self.c = tk.Canvas(body, width=930, height=550, bg=BG, highlightthickness=0)
        self.c.pack(side="left", padx=6, pady=6)
        self.draw_scene()

        right = tk.Frame(body, bg=BG)
        right.pack(side="left", fill="y", padx=(0, 6), pady=6)
        self.prog_text = self.mk_text(right, "Program (address: word  instruction)", 36, 14)
        self.mem_text = self.mk_text(right, "Data memory (first 32 words)", 36, 8)
        self.log_text = self.mk_text(right, "Cycle log", 36, 12)
        self.prog_text.tag_config("cur", background=ACT_FILL, foreground="#ffffff")
        self.mem_text.tag_config("hit", background=ROW_ACT, foreground="#ffffff")

        self.status = tk.Label(self.root, text="", anchor="w", bg=PANEL, fg=VALUE, font=F_MONO)
        self.status.pack(fill="x")

    def mk_text(self, parent, title, width, height):
        tk.Label(parent, text=title, bg=BG, fg=TITLE, font=F_TITLE, anchor="w").pack(fill="x")
        t = tk.Text(parent, width=width, height=height, bg=PANEL, fg=VALUE,
                    font=F_MONO, relief="flat", wrap="none", state="disabled")
        t.pack(fill="x", pady=(0, 6))
        return t

    def draw_scene(self):
        c = self.c
        for name, pts in WIRES.items():
            flat = [v for p in pts for v in p]
            self.wires[name] = c.create_line(*flat, fill=WIRE, width=2, arrow=tk.LAST,
                                             arrowshape=(9, 11, 4))
            self.wire_geo[name] = path_geometry(pts)

        for name, (x1, y1, x2, y2, title) in BLOCKS.items():
            self.rects[name] = c.create_rectangle(x1, y1, x2, y2, fill=BLOCK,
                                                  outline=OUTLINE, width=2)
            c.create_text((x1 + x2) / 2, y1 + 11, text=title, fill=TITLE, font=F_TITLE)
            if name in ("RF", "CTRL"):
                continue
            self.vals[name] = c.create_text((x1 + x2) / 2, (y1 + y2) / 2 + 7, text="",
                                            fill=VALUE, font=F_VAL, justify="center")

        # Register file rows
        for i in range(8):
            y0 = 212 + i * 20
            r = c.create_rectangle(206, y0, 344, y0 + 18, fill=BLOCK2, outline="")
            c.create_text(222, y0 + 9, text="r%d" % i, fill=TITLE, font=F_SMALL)
            v = c.create_text(300, y0 + 9, text="0000", fill=VALUE, font=F_SMALL)
            self.row_rects.append(r)
            self.row_vals.append(v)

        # FSM state boxes
        for i, name in enumerate(STATE_NAMES):
            col, row = i % 3, i // 3
            x = 630 + col * 76
            y = 52 + row * 36
            r = c.create_rectangle(x, y, x + 70, y + 26, fill=BLOCK2, outline=OUTLINE, width=1)
            c.create_text(x + 35, y + 13, text=name, fill=VALUE, font=("Helvetica", 8, "bold"))
            self.state_rects.append(r)

        c.create_text(20, 530, anchor="w", fill=TITLE, font=F_MONO,
                      text="Yellow dots: data on the buses.  Green blocks: active this cycle.")

    # ----------------------------------------------------------- program load
    def load_source(self, text, quiet=False):
        try:
            words = assemble(text)
        except AsmError as e:
            if not quiet:
                messagebox.showerror("Assembler error", str(e))
            return False
        self.abort_anim()
        self.mode = "idle"
        self.btn_run.config(text="Run")
        self.source = text
        self.words = words
        self.cpu.load(words)
        self.set_text(self.log_text, "")
        lines = ["%03X: %04X  %s" % (i, w, disasm(w, i)) for i, w in enumerate(words)]
        self.set_text(self.prog_text, "\n".join(lines))
        self.refresh()
        return True

    def on_demo(self, name):
        self.load_source(DEMOS[name])
        if self.editor is not None:
            self.editor_text.delete("1.0", "end")
            self.editor_text.insert("1.0", self.source)

    def open_editor(self):
        if self.editor is not None:
            self.editor.lift()
            return
        top = tk.Toplevel(self.root)
        top.title("Assembly editor")
        top.configure(bg=BG)
        self.editor = top
        self.editor_text = tk.Text(top, width=70, height=26, bg=PANEL, fg=VALUE,
                                   insertbackground=VALUE, font=("Courier", 10), relief="flat")
        self.editor_text.pack(fill="both", expand=True, padx=6, pady=6)
        self.editor_text.insert("1.0", self.source)
        row = tk.Frame(top, bg=BG)
        row.pack(fill="x", pady=(0, 6))

        def assemble_load():
            self.load_source(self.editor_text.get("1.0", "end"))

        def close():
            self.editor = None
            top.destroy()

        for text, cmd in (("Assemble and Load", assemble_load), ("Close", close)):
            tk.Button(row, text=text, command=cmd, bg=BTN, fg=BTN_FG, relief="flat",
                      padx=10, pady=4, font=F_UI).pack(side="left", padx=6)
        tk.Label(row, bg=BG, fg=TITLE, font=F_MONO,
                 text="Instructions: NOP HALT ADD SUB AND OR XOR SHL SHR ADDI LDI LHI LD ST BEQ BNE JMP").pack(side="left")
        top.protocol("WM_DELETE_WINDOW", close)

    def export_hex(self):
        path = filedialog.asksaveasfilename(defaultextension=".hex", initialfile="program.hex",
                                            filetypes=[("HEX file", "*.hex")])
        if not path:
            return
        with open(path, "w") as f:
            for w in self.words:
                f.write("%04X\n" % w)
        messagebox.showinfo("Export HEX", "Saved %d words to %s" % (len(self.words), path))

    # ---------------------------------------------------------------- helpers
    def set_text(self, widget, text):
        widget.config(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.config(state="disabled")

    def set_block(self, name, active):
        if name in self.rects:
            self.c.itemconfig(self.rects[name],
                              fill=ACT_FILL if active else BLOCK,
                              outline=ACT_OUT if active else OUTLINE)

    def cycle_seconds(self):
        return (1800.0 / self.speed.get()) / 1000.0

    # ---------------------------------------------------------------- refresh
    def refresh(self):
        cpu = self.cpu
        c = self.c
        v = self.vals
        c.itemconfig(v["PC"], text="%04X" % cpu.pc)
        c.itemconfig(v["ADD1"], text="%04X" % ((cpu.pc + 1) & MASK))
        c.itemconfig(v["IMEM"], text="[%03X]\n%04X" % (cpu.pc & 0xFFF, cpu.imem[cpu.pc & 0xFFF]))
        c.itemconfig(v["IR"], text="%04X\n%s" % (cpu.ir, disasm(cpu.ir, cpu.ir_addr)))
        c.itemconfig(v["A"], text="%04X" % cpu.a)
        c.itemconfig(v["B"], text="%04X" % cpu.b)
        c.itemconfig(v["ALU"], text=ALU_NAMES[cpu.last_alu_op])
        c.itemconfig(v["ALUOUT"], text="%04X" % cpu.alu_out)
        c.itemconfig(v["FLAGS"], text="Z%d N%d C%d" % (cpu.z, cpu.n, cpu.c))
        c.itemconfig(v["MDR"], text="%04X" % cpu.mdr)
        if cpu.last_mem:
            addr, val, rw = cpu.last_mem
            c.itemconfig(v["DMEM"], text="last %s [%03X]\n= %04X" % (rw, addr, val))
        else:
            c.itemconfig(v["DMEM"], text="no access yet")
        for i in range(8):
            c.itemconfig(self.row_vals[i], text="%04X" % cpu.regs[i])
        for i, r in enumerate(self.state_rects):
            if i == cpu.state:
                c.itemconfig(r, fill=BLOCK2, outline=WIRE_ACT, width=3)
            else:
                c.itemconfig(r, fill=BLOCK2, outline=OUTLINE, width=1)

        # Program listing highlight
        self.prog_text.tag_remove("cur", "1.0", "end")
        cur = cpu.pc if cpu.state == S_FETCH else cpu.ir_addr
        if cpu.state == S_HALT:
            cur = cpu.ir_addr
        if 0 <= cur < cpu.prog_len:
            self.prog_text.tag_add("cur", "%d.0" % (cur + 1), "%d.end" % (cur + 1))
            self.prog_text.see("%d.0" % (cur + 1))

        # Data memory view
        rows = []
        for r in range(8):
            rows.append("%03X: " % (r * 4) + " ".join("%04X" % cpu.dmem[r * 4 + k] for k in range(4)) + " ")
        self.set_text(self.mem_text, "\n".join(rows))
        self.mem_text.tag_remove("hit", "1.0", "end")
        if cpu.last_mem and cpu.last_mem[2] == "W" and cpu.last_mem[0] < 32:
            a = cpu.last_mem[0]
            line = a // 4 + 1
            col = 5 + (a % 4) * 5
            self.mem_text.tag_add("hit", "%d.%d" % (line, col), "%d.%d" % (line, col + 4))

        self.status.config(text=" Cycle %d | Instructions %d | Next state: %s | PC %04X | CPI %s" % (
            cpu.cycles, cpu.instructions, STATE_NAMES[cpu.state], cpu.pc,
            ("%.2f" % (cpu.cycles / cpu.instructions)) if cpu.instructions else "-"))

    def log(self, info):
        t = self.log_text
        t.config(state="normal")
        t.insert("end", "%04d %-9s %s\n" % (self.cpu.cycles, STATE_NAMES[info["state"]], info["msg"]))
        t.see("end")
        t.config(state="disabled")

    # -------------------------------------------------------------- animation
    def start_anim(self, info):
        self.anim = {"t0": time.perf_counter(), "dur": self.cycle_seconds(),
                     "info": info, "packets": {}}
        for n in info["active"]:
            self.set_block(n, True)
        self.c.itemconfig(self.state_rects[info["state"]], fill=ACT_FILL, outline=ACT_OUT, width=2)
        for r in info["rows"]:
            self.c.itemconfig(self.row_rects[r], fill=ROW_ACT)
        for w in set(t[0] for t in info["transfers"]):
            self.c.itemconfig(self.wires[w], fill=WIRE_ACT, width=3)
        self.log(info)
        self.animate()

    def animate(self):
        a = self.anim
        if a is None:
            return
        frac = (time.perf_counter() - a["t0"]) / a["dur"]
        if frac >= 1.0:
            self.finish_anim()
            return
        for idx, (w, label, s, e) in enumerate(a["info"]["transfers"]):
            pk = a["packets"].get(idx)
            if s <= frac <= e:
                p = (frac - s) / (e - s)
                x, y = point_on_path(*self.wire_geo[w], p)
                if pk is None:
                    oval = self.c.create_oval(x - 6, y - 6, x + 6, y + 6, fill=PKT, outline="")
                    txt = self.c.create_text(x, y - 15, text=label, fill=PKT_TEXT, font=F_SMALL)
                    a["packets"][idx] = [oval, txt]
                else:
                    self.c.coords(pk[0], x - 6, y - 6, x + 6, y + 6)
                    self.c.coords(pk[1], x, y - 15)
            elif frac > e and pk is not None:
                self.c.delete(pk[0])
                self.c.delete(pk[1])
                a["packets"][idx] = None
        self.root.after(16, self.animate)

    def abort_anim(self):
        a = self.anim
        if a is None:
            return
        for pk in a["packets"].values():
            if pk:
                self.c.delete(pk[0])
                self.c.delete(pk[1])
        for n in a["info"]["active"]:
            self.set_block(n, False)
        for w in set(t[0] for t in a["info"]["transfers"]):
            self.c.itemconfig(self.wires[w], fill=WIRE, width=2)
        for r in a["info"]["rows"]:
            self.c.itemconfig(self.row_rects[r], fill=BLOCK2)
        self.anim = None

    def finish_anim(self):
        self.abort_anim()
        self.refresh()
        self.after_anim()

    # --------------------------------------------------------------- stepping
    def do_step(self):
        if self.cpu.state == S_HALT:
            self.mode = "idle"
            self.btn_run.config(text="Run")
            return
        info = self.cpu.step()
        self.start_anim(info)

    def after_anim(self):
        if self.mode == "run":
            if self.cpu.state == S_HALT:
                self.mode = "idle"
                self.btn_run.config(text="Run")
            else:
                self.do_step()
        elif self.mode == "instr":
            if self.cpu.state in (S_FETCH, S_HALT):
                self.mode = "idle"
            else:
                self.do_step()
        else:
            self.mode = "idle"

    def on_step_cycle(self):
        if self.anim is not None or self.mode != "idle":
            return
        self.mode = "cycle"
        self.do_step()

    def on_step_instr(self):
        if self.anim is not None or self.mode != "idle":
            return
        self.mode = "instr"
        self.do_step()

    def on_run(self):
        if self.mode == "run":
            self.mode = "idle"
            self.btn_run.config(text="Run")
            return
        if self.anim is not None or self.mode != "idle":
            return
        if self.cpu.state == S_HALT:
            return
        self.mode = "run"
        self.btn_run.config(text="Pause")
        self.do_step()

    def on_reset(self):
        self.abort_anim()
        self.mode = "idle"
        self.btn_run.config(text="Run")
        self.cpu.reset()
        self.set_text(self.log_text, "")
        self.refresh()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
