/* SPDX-License-Identifier: MIT */
/* DSP-side mailbox for the RAM NCO diagnostic. See ../README.md. */
#include <stdint.h>
#define S ((volatile uint32_t *)0x00205f00u)
#define INPUT ((const uint32_t *)0x00206000u)
#define OUTPUT ((uint32_t *)0x00208000u)
#define PACKED ((uint32_t *)0x0020a000u)
extern void pack_words(const void *,void *,uint32_t);
extern uint32_t nco_asm(uint32_t,uint32_t,uint32_t);
extern uint32_t nco_pack_asm(uint32_t,uint32_t,uint32_t,const uint32_t *,uint32_t *);
static uint32_t cycles(void) { uint32_t v;asm volatile("rdcycle %0":"=r"(v));return v; }
void main(void) {
    S[0]=0x4e434f31u;S[1]=0;S[3]=0;S[12]=0;
    for (;;) {
        S[1]++;
        uint32_t req=S[2];if(req==S[3])continue;
        uint32_t mode=S[4],n=S[5],phase=S[6],step=S[7],repeat=S[8],bank=S[20];
        if (mode>2 || !n || n>2048 || (n&15) || !repeat || repeat>4096 || bank>1) {
            S[11]=0xffffffff;S[3]=req;continue;
        }
        uint32_t begin=cycles(),clips=0;
        for(uint32_t r=0;r<repeat;r++) {
            if(mode==1) clips+=nco_pack_asm(phase,step,n,INPUT+bank*2048,PACKED+bank*2048);
            else if(mode==0) clips+=nco_asm(phase,step,n);
            else pack_words(INPUT,PACKED,n*4);
            phase+=n*step;
        }
        S[9]=cycles()-begin;S[10]=phase;S[11]=clips;
        asm volatile("fence":::"memory");S[3]=req;
    }
}
