/* SPDX-License-Identifier: MIT */
/* XR1710G RAM-only throughput diagnostic, WM entry at 0x0090b000.
 * Uses an initialized radio, custom DSP probe image, idle host DMA ring,
 * and a host stream consumer. Repeats a known 2048-sample UMAC pool block;
 * this is not a scheduler for continuous ICAP capture. Addresses and ROM
 * entry point are specific to the tested MT7996 firmware/driver combination.
 * Reset the wireless subsystem after use, including all error exits.
 */
#include <stdint.h>
#define R(a) (*(volatile uint32_t *)(uintptr_t)(a))
#define H(a) (*(volatile uint16_t *)(uintptr_t)(a))
#define OUT 0x0090e200u
#define TX 0x0090e000u
#define RX 0x0090e100u
#define D 0x58000000u
#define A 0x0222c608u
#define GO 0x0090f008u
#define CTL(b) (0x86205f00u+(b)*0x400000u)
#define IN(b,s) (0x86206000u+(b)*0x400000u+(s)*0x2000u)
#define PACK(b,s) (0x8620a000u+(b)*0x400000u+(s)*0x2000u)
#define NONE 0xffffffffu
static uint32_t cycle(void) { uint32_t v;asm volatile("rdcycle %0":"=r"(v));return v; }
static void fence(void) { asm volatile("fence":::"memory"); }
static void reclaim(void) {
    uint32_t saved;
    asm volatile("mv %0,gp":"=r"(saved));
    asm volatile("li gp,0x02212800;li a0,0;li a1,0;jalr ra,0(%[f]);mv gp,%[s]"
      ::[f]"r"(0x00825010),[s]"r"(saved)
      :"ra","a0","a1","a2","a3","a4","a5","a6","a7","t0","t1","t2","t3","t4","t5","t6","memory");
}
/* Buffer ownership: free -> input DMA -> ready -> DSP -> output -> PCIe.
 * A buffer is reusable only after the corresponding PCIe DIDX advances.
 * MEM_DMA has one outstanding input, with immutable RX destinations.
 * Twelve entries are divisible by 2*cores for cores=1,2,3.
 */
__attribute__((section(".text.entry"))) uint32_t entry(uint32_t cores,uint32_t count,uint32_t step,uint32_t unused) {
    uint32_t cpu=R(0x54000308),desc=R(0x54000300),slots=R(A+8),cfg=R(D+0x208);
    uint32_t start=cycle(),status=0,done=0,idx=0,clips=0,read=cpu;
    uint32_t state[6]={0},jobs[6]={0},active[3]={NONE,NONE,NONE};
    uint32_t nextjob[3]={0,1,2},owners[8]={0};
    uint32_t ticket[3]={0};
    uint32_t input=0,send=0,moving=NONE,last=start,loops=0;
    for(uint32_t i=0;i<16;i++)R(OUT+4*i)=0;
    if(!cores || cores>3 || count<cores || count>65536)return 100;
    if(cpu!=R(0x5400030c) || cpu!=R(A+0x2c) || (cfg&10))return 101;
    for(uint32_t b=0;b<cores;b++) {
        uint32_t ctl=CTL(b);
        ticket[b]=R(ctl+12);
        if(R(ctl)!=0x4e434f31 || R(ctl+48) || R(ctl+64) || R(ctl+8)!=ticket[b])return 102;
    }
    asm volatile("csrwi 0x7cc,6;fence":::"memory");
    R(D+0x208)=cfg&~5u;
    for(uint32_t i=0;i<12;i++) {
        R(TX+i*16)=0xe0400000;R(TX+i*16+4)=0x80000000;R(TX+i*16+8)=0;R(TX+i*16+12)=0;
        R(RX+i*16)=IN(i%cores,(i/cores)&1);R(RX+i*16+4)=8192u<<16;R(RX+i*16+8)=0;R(RX+i*16+12)=0;
    }
    R(D+0x300)=TX;R(D+0x304)=12;R(D+0x308)=0;R(D+0x500)=RX;R(D+0x504)=12;R(D+0x508)=11;
    R(D+0x20c)=1;R(D+0x280)=1;fence();R(D+0x208)=cfg|0x2045;
    while(R(GO)!=0x474f474f)if(cycle()-start>2160000000u){status=1;goto finish;}
    R(GO)=0;start=last=cycle();
    while(done<count) {
        uint32_t progress=0,didx=R(0x5400030c);
        loops++;
        while(read!=didx) {
            uint32_t id=owners[read];
            if(state[id]!=5){status=20;goto finish;}
            state[id]=0;read=(read+1)&7;done++;progress=1;
        }
        if(moving!=NONE) {
            uint32_t next=(idx+1)%12;
            if(R(D+0x30c)==next && R(D+0x50c)==next && (R(RX+idx*16+4)&0x80000000u)) {
                if((R(RX+idx*16+4)&0xbfff0000u)!=0xa0000000u){status=12;goto finish;}
                R(RX+idx*16+4)=8192u<<16;fence();R(D+0x508)=idx;
                state[moving]=2;moving=NONE;idx=next;progress=1;
            }
        }
        for(uint32_t b=0;b<cores;b++) {
            uint32_t ctl=CTL(b),job=active[b];
            if(job!=NONE && (R(ctl+48) || R(ctl+64))){status=13;goto finish;}
            if(job!=NONE && R(ctl+12)==ticket[b]) {
                if(R(ctl+40)!=(job+1)*2048u*step){status=15;goto finish;}
                clips+=R(ctl+44);state[job%(2*cores)]=4;active[b]=NONE;progress=1;
            }
            job=nextjob[b];
            if(active[b]==NONE && job<count) {
                uint32_t id=job%(2*cores);
                if(state[id]==2 && jobs[id]==job) {
                    R(ctl+16)=1;R(ctl+20)=2048;R(ctl+24)=job*2048u*step;R(ctl+28)=step;R(ctl+32)=1;
                    /* Fresh per-core sequence even across consecutive runs. */
                    R(ctl+80)=id/cores;ticket[b]++;fence();R(ctl+8)=ticket[b];
                    state[id]=3;active[b]=job;nextjob[b]+=cores;progress=1;
                }
            }
        }
        if(send<count) {
            uint32_t id=send%(2*cores),next=(cpu+1)&7;
            if(state[id]==4 && jobs[id]==send && next!=read) {
                R(slots+cpu*4)=0;R(desc+cpu*16)=PACK(id%cores,id/cores);H(desc+cpu*16+6)=0x4000|7168;
                owners[cpu]=id;state[id]=5;fence();cpu=next;R(0x54000308)=cpu;send++;progress=1;
            }
        }
        if(moving==NONE && input<count) {
            uint32_t id=input%(2*cores);
            if(state[id]==0) {
                jobs[id]=input;state[id]=1;moving=id;
                R(TX+idx*16+4)=(8192u<<16)|0x40000000u;fence();R(D+0x308)=(idx+1)%12;
                input++;progress=1;
            }
        }
        if(progress)last=cycle();
        else if(cycle()-last>72000000u){status=14;goto finish;}
    }
    R(OUT+8)=cycle()-start;
    R(A+0x2c)=cpu;R(A+0x34)+=done;if(done>=8)R(A+0x38)+=done-8;
    reclaim();
finish:
    R(OUT)=status;R(OUT+4)=done;R(OUT+12)=R(0x54000110);R(OUT+16)=R(0x54000308);R(OUT+20)=R(0x5400030c);
    R(OUT+24)=R(A+0x34);R(OUT+28)=R(A+0x38);R(OUT+32)=R(D+0x110);R(OUT+36)=clips;
    R(OUT+40)=R(D+0x30c);R(OUT+44)=R(D+0x50c);R(OUT+48)=loops;R(OUT+52)=input;R(OUT+56)=send;
    return status;
}
