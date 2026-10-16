/* SPDX-License-Identifier: MIT */
/* MT7996 MRV5 diagnostic NCO kernel, measured on all three XR1710G DSPs.
 * a0: initial uint32 phase; a1: uint32 phase step; a2: sample count.
 * Returns number of saturated I/Q components in a0; caller advances phase
 * by count * step modulo 2^32. Count must be 16..2048, a multiple of 16.
 *
 * DSP-local buffers: input 0x206000, output 0x208000, packed Q15 cos/sin
 * LUT 0x201000 (4096 uint32 entries, cosine in the high halfword). Input
 * and output words contain signed 14-bit I at [29:16], Q at [13:0].
 * LUT index = ((phase + 0x80000) mod 2^32) >> 20. Each component is rounded
 * half-up after Q15 multiplication and saturated to [-8192,8191].
 *
 * Requires initialized DSP windows, exclusive ownership of both custom MAC
 * accumulators, and interrupts disabled by the caller. Standard callee-saved
 * integer registers are preserved; custom accumulators are overwritten.
 * See README.md. This kernel is not enabled by the normal firmware build.
 */
.text
.balign 4
.global nco_asm
nco_asm:
    addi sp,sp,-48
    sw s0,0(sp)
    sw s1,4(sp)
    sw s2,8(sp)
    sw s3,12(sp)
    sw s4,16(sp)
    sw s5,20(sp)
    sw s6,24(sp)
    sw s7,28(sp)
    sw s8,32(sp)
    li s0,0
    li a3,0x206000
    li a4,0x208000
    slli a2,a2,2
    add s1,a3,a2
    li a5,0x201000
    li a6,15
    li a7,0x3fff
    li s2,8192
    li t0,0x80000
    add a0,a0,t0
    lw s8,0(a3)
    srli t0,a0,18
    andi t0,t0,-4
    add t0,t0,a5
    lh t1,2(t0)
    lh t2,0(t0)
    slli t3,s8,2
    slli t4,s8,18
    srai t3,t3,18
    srai t4,t4,18
.macro PIPE off,co,si,ii,qq,nco,nsi,ni,nq
    /* While the MAC results become available, prepare the next sample. */
    .insn r 0x7b,0,0x60,x0,\ii,\co
    .insn r 0x7b,0,0x60,x1,\ii,\si
    add a0,a0,a1
    .insn r 0x7b,3,0x40,x0,\qq,\si
    .insn r 0x7b,1,0x40,x1,\qq,\co
    .if \off == 60
    /* Do not prefetch beyond the input block, including the 16-sample case. */
    addi a2,a3,64
    beq a2,s1,.Lskipread\@
    .endif
    lw s8,\off+4(a3)
.Lskipread\@:
    srli t0,a0,18
    andi t0,t0,-4
    add t0,t0,a5
    lh \nco,2(t0)
    lh \nsi,0(t0)
    .insn r 0x7b,6,2,t5,x0,a6
    .insn r 0x7b,6,2,t6,x1,a6
    slli \ni,s8,2
    slli \nq,s8,18
    srai \ni,\ni,18
    srai \nq,\nq,18
    add \ii,t5,s2
    add \qq,t6,s2
    srli \ii,\ii,14
    srli \qq,\qq,14
    or t0,\ii,\qq
    bnez t0,.Lclip\@
.Lresume\@:
    and t5,t5,a7
    and t6,t6,a7
    slli t5,t5,16
    or t5,t5,t6
    sw t5,\off(a4)
    .pushsection .text.slow,"ax"
.Lclip\@:
    /* sat14(x) = sat16(x << 2) >> 2 for this kernel's bounded results. */
    slli t5,t5,2
    slli t6,t6,2
    .insn r 0x5b,4,2,t5,t5,x0
    .insn r 0x5b,4,2,t6,t6,x0
    srai t5,t5,2
    srai t6,t6,2
    snez \ii,\ii
    snez \qq,\qq
    add s0,s0,\ii
    add s0,s0,\qq
    j .Lresume\@
    .popsection
.endm
1:
    .set OFF,0
    .rept 8
    PIPE OFF,t1,t2,t3,t4,s4,s5,s6,s7
    PIPE OFF+4,s4,s5,s6,s7,t1,t2,t3,t4
    .set OFF,OFF+8
    .endr
    addi a3,a3,64
    addi a4,a4,64
    bne a3,s1,1b
    mv a0,s0
    lw s0,0(sp)
    lw s1,4(sp)
    lw s2,8(sp)
    lw s3,12(sp)
    lw s4,16(sp)
    lw s5,20(sp)
    lw s6,24(sp)
    lw s7,28(sp)
    lw s8,32(sp)
    addi sp,sp,48
    ret
