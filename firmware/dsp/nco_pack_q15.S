/* SPDX-License-Identifier: MIT */
/* Fused Q15 NCO and 28-bit IQ packing for the MT7996 MRV5 DSP.
 * a0=phase, a1=step, a2=count (16..2048, multiple of 16),
 * a3=input words, a4=packed output. Returns saturated component count.
 * Input, LUT, rounding and saturation match nco_q15.S. Output matches
 * stream/pack.S byte for byte. Buffers must not overlap; LUT is 0x201000.
 * Exclusive MAC ownership and disabled interrupts are required.
 * Each group processes sample 7 first, then 0..6, so sample 7's spare-bit
 * payload is ready before any output store. Phase follows the same sample
 * indices, including uint32 wrap. No intermediate output array is needed.
 */
.text
.balign 4
.global nco_pack_asm
nco_pack_asm:
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
    sw s9,36(sp)
    sw s10,40(sp)
    sw s11,44(sp)
    li s0,0
    slli a2,a2,2
    add s1,a3,a2
    li a5,0x201000
    li a6,15
    li a7,0x3fff
    li s2,8192
    li s11,16384
    li s10,0xc000c000
    slli a2,a1,3
    add s3,a2,a1
    sub a2,a2,a1
    add a0,a0,a2
    li t0,0x80000
    add a0,a0,t0
    lw s8,28(a3)
    srli t0,a0,18
    andi t0,t0,-4
    add t0,t0,a5
    lh t1,2(t0)
    lh t2,0(t0)
    slli t3,s8,2
    slli t4,s8,18
    srai t3,t3,18
    srai t4,t4,18
.macro PACK_PIPE idx,next,co,si,ii,qq,nco,nsi,ni,nq
    .insn r 0x7b,0,0x60,x0,\ii,\co
    .insn r 0x7b,0,0x60,x1,\ii,\si
    .if (\idx % 8) == 7
    sub a0,a0,a2
    .elseif (\idx % 8) == 6
    add a0,a0,s3
    .else
    add a0,a0,a1
    .endif
    .insn r 0x7b,3,0x40,x0,\qq,\si
    .insn r 0x7b,1,0x40,x1,\qq,\co
    .if \idx == 14
    addi t0,a3,64
    beq t0,s1,.Lpacked_skipread\@
    .endif
    lw s8,(\next*4)(a3)
.Lpacked_skipread\@:
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
    or t0,\ii,\qq
    bgeu t0,s11,.Lpacked_clip\@
.Lpacked_resume\@:
    and t5,t5,a7
    and t6,t6,a7
    slli t5,t5,16
    or t5,t5,t6
    .if (\idx % 8) == 7
    mv s9,t5
    .else
    slli t6,s9,(14-2*(\idx%8))
    and t6,t6,s10
    or t5,t5,t6
    sw t5,((\idx-\idx/8)*4)(a4)
    .endif
    .pushsection .text.slow,"ax"
.Lpacked_clip\@:
    srli \ii,\ii,14
    srli \qq,\qq,14
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
    j .Lpacked_resume\@
    .popsection
.endm
1:
    PACK_PIPE 7,0,t1,t2,t3,t4,s4,s5,s6,s7
    PACK_PIPE 0,1,s4,s5,s6,s7,t1,t2,t3,t4
    PACK_PIPE 1,2,t1,t2,t3,t4,s4,s5,s6,s7
    PACK_PIPE 2,3,s4,s5,s6,s7,t1,t2,t3,t4
    PACK_PIPE 3,4,t1,t2,t3,t4,s4,s5,s6,s7
    PACK_PIPE 4,5,s4,s5,s6,s7,t1,t2,t3,t4
    PACK_PIPE 5,6,t1,t2,t3,t4,s4,s5,s6,s7
    PACK_PIPE 6,15,s4,s5,s6,s7,t1,t2,t3,t4
    PACK_PIPE 15,8,t1,t2,t3,t4,s4,s5,s6,s7
    PACK_PIPE 8,9,s4,s5,s6,s7,t1,t2,t3,t4
    PACK_PIPE 9,10,t1,t2,t3,t4,s4,s5,s6,s7
    PACK_PIPE 10,11,s4,s5,s6,s7,t1,t2,t3,t4
    PACK_PIPE 11,12,t1,t2,t3,t4,s4,s5,s6,s7
    PACK_PIPE 12,13,s4,s5,s6,s7,t1,t2,t3,t4
    PACK_PIPE 13,14,t1,t2,t3,t4,s4,s5,s6,s7
    PACK_PIPE 14,23,s4,s5,s6,s7,t1,t2,t3,t4
    addi a3,a3,64
    addi a4,a4,56
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
    lw s9,36(sp)
    lw s10,40(sp)
    lw s11,44(sp)
    addi sp,sp,48
    ret
