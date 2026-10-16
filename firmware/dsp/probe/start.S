/* SPDX-License-Identifier: MIT */
/* RAM diagnostic startup: initialize MRV5 windows and disable interrupts. */
.option norvc
.section .text.entry,"ax"
.globl _start
_start:
    li t0, 0x07807800
    csrw mstatus, t0
    csrw mie, zero
    csrw 0x9dc, zero
    li t0, 0x10000
    csrw 0x9de, t0
    li t0, 0x200000
    csrw 0x9dd, t0
    li t0, 0x210000
    csrw 0x9df, t0
    li t0, 12
    csrs 0x7c0, t0
    li t0, 0xc0
    csrw 0x9e0, t0
    li t0, 0x10000
    csrw 0x9f0, t0
    li t0, 0x2000c0
    csrw 0x9e1, t0
    li t0, 0x210000
    csrw 0x9f1, t0
    li t0, 0x400000c0
    csrw 0x9e2, t0
    li t0, 0x80000000
    csrw 0x9f2, t0
    li t0, 0x800002c0
    csrw 0x9e3, t0
    li t0, 0xc0000000
    csrw 0x9f3, t0
    li t0, 0xc00003c0
    csrw 0x9e4, t0
    li t0, -1
    csrw 0x9f4, t0
    li t0, 31
    csrw 0x9c0, t0
    li t0, 0x37fc
    csrw 0x7c0, t0
    fence.i
    la t0, fault
    csrw mtvec, t0
    li t0, 31
    csrs 0xbc0, t0
    li sp, 0x205e00
    call main
fault:
    li t0, 0x205f00
    li t1, 1
    sw t1, 48(t0)
    csrr t1, mcause
    sw t1, 52(t0)
    csrr t1, mepc
    sw t1, 56(t0)
    csrr t1, mtval
    sw t1, 60(t0)
1:  j 1b
