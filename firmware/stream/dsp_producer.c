/* SPDX-License-Identifier: MIT */
/* Continuous ICAP -> MEM_DMA -> three DSPs -> MCU_DMA0. All input/output
 * ownership is explicit. Pool sources repeat over 128 slots; destinations
 * repeat over six independent RX slots, so neither ring changes an address
 * that hardware might already have prefetched. No WA packer is started.
 */
#include "hw/mt7996/mcu_dma.h"
#include "hw/mt7996/dsp.h"
#include "stream.h"
#include "ctl.h"
#include "dsp_nco.h"
#define D MT7996_MEM_DMA_BASE
#define NONE 0xffffffffu
#define ADAPTER MT7996_MCU_DMA0_TX0_ADAPTER
#define TXBASE MT7996_MCU_DMA0_TX0_BASE
#define TXCPU MT7996_MCU_DMA0_TX0_CIDX
#define TXDMA MT7996_MCU_DMA0_TX0_DIDX

static void fence(void) { asm volatile("fence" ::: "memory"); }
static void reclaim(void)
{
	uint32_t saved;
	asm volatile("mv %0,gp":"=r"(saved));
	asm volatile("li gp,0x02212800;li a0,0;li a1,0;jalr ra,0(%[f]);mv gp,%[s]"
		::[f]"r"(MT7996_MCU_ROM_DMA_RECLAIM),[s]"r"(saved)
		:"ra","a0","a1","a2","a3","a4","a5","a6","a7","t0","t1","t2","t3","t4","t5","t6","memory");
}

__attribute__((used)) void producer(void)
{
	uint32_t cpu=R32(TXCPU), read=cpu, desc=R32(TXBASE), slots=R32(ADAPTER+8);
	uint32_t cfg=R32(D+0x208), status=0, g0=rdcycle(), last=g0, max_lag=0;
	uint32_t state[6]={0}, phase_at[6], step_at[6], active[3]={NONE,NONE,NONE};
	uint32_t ticket[3], bank[3]={0}, owners[8]={0};
	uint32_t in_id=0, out_id=0, txi=0, rxi=0, moving=NONE, phase=0, prev=0;
	uint32_t step=R32(MT7996_PROBE_OWN_WM_RADIO_ARG(1)), run=R32(MT7996_PROBE_OWN_WM_RADIO_ARG(2));
	uint64_t assigned=0, consumed=0, published=0, completed=0, lap=0, written=0, start=0, clips=0;
	uint32_t mem_started=0, capture_started=0, stopping=0, stalled=0;
	struct ctl ctl;
	for (uint32_t i=0;i<sizeof(struct res)/4;i++) ((volatile uint32_t *)RES)[i]=0;
	R32(NCO_REG(N_MAGIC))=NCO_MAGIC;R32(NCO_REG(N_RUN))=run;R32(NCO_REG(N_RUNNING))=0;
	R32(NCO_REG(N_ACK))=R32(NCO_REG(N_REQ));R32(NCO_REG(N_STATUS))=N_STOPPED;
	R32(NCO_REG(N_APPLIED_STEP))=step;R32(NCO_REG(N_DMA_ERROR))=0;
	R32(NCO_REG(N_BOUNDARY_LO))=0;R32(NCO_REG(N_BOUNDARY_HI))=0;R32(NCO_REG(N_PHASE))=0;
	R32(NCO_REG(N_CLIPS_LO))=0;R32(NCO_REG(N_CLIPS_HI))=0;
	R32(NCO_REG(N_ASSIGNED_LO))=0;R32(NCO_REG(N_ASSIGNED_HI))=0;R32(NCO_REG(N_NOW))=g0;
	if(cpu!=R32(TXDMA)){status=4;goto finish;}
	if(cpu!=R32(ADAPTER+0x2c)){status=5;goto finish;}
	if(cfg&10){status=12;goto finish;}
	/* Restart initialized DSP code after radio setup, before touching DTCM. */
	R32(MT7996_DSP_CORE_ENABLE)=0;R32(MT7996_DSP_CORE_RESET)=7;
	R32(MT7996_DSP_CORE_ENABLE)=7;R32(MT7996_DSP_CORE_RESET)=0;
	for(uint32_t b=0;b<3;b++) {
		uint32_t c=DSP_CTL(b), beat;
		while(R32(c)!=0x4e434f31u) if(rdcycle()-g0>72000000u){status=11;goto finish;}
		beat=R32(c+4);
		while(R32(c+4)==beat) if(rdcycle()-g0>72000000u){status=11;goto finish;}
		if(R32(c+48)){status=11;goto finish;}
		/* A reset may leave a preceding request in DTCM. Wait for it to finish. */
		while(R32(c+8)!=R32(c+12)) if(rdcycle()-g0>72000000u){status=11;goto finish;}
		ticket[b]=R32(c+12);
	}
	cache_wbinval_all();
	R32(D+0x208)=cfg&~5u;
	for(uint32_t i=0;i<128;i++) {
		R32(DSP_TX+16*i)=ring_at(i);R32(DSP_TX+16*i+4)=0x80000000;
		R32(DSP_TX+16*i+8)=0;R32(DSP_TX+16*i+12)=0;
	}
	for(uint32_t i=0;i<6;i++) {
		R32(DSP_RX+16*i)=DSP_IN(i%3,i/3);R32(DSP_RX+16*i+4)=CHUNK<<16;
		R32(DSP_RX+16*i+8)=0;R32(DSP_RX+16*i+12)=0;
	}
	R32(D+0x300)=DSP_TX;R32(D+0x304)=128;R32(D+0x308)=0;
	R32(D+0x500)=DSP_RX;R32(D+0x504)=6;R32(D+0x508)=5;
	R32(D+0x20c)=1;R32(D+0x280)=1;fence();R32(D+0x208)=cfg|0x2045;
	mem_started=1;g0=rdcycle();
	while(R32(GO_ADDR)!=GO_MAGIC) if(rdcycle()-g0>GO_CYCLES){status=2;goto drain;}
	RES->go_wait_cycles=rdcycle()-g0;R32(GO_ADDR)=0;ctl_init(&ctl);
	R32(MT7996_ICAP_CR0(0))=MT7996_ICAP_CR0_HELD;
	R32(MT7996_ICAP_CR0(0))=MT7996_ICAP_CR0_RING_IDLE;
	R32(MT7996_ICAP_CR0(0))=MT7996_ICAP_CR0_RING_ARM;
	R32(MT7996_ICAP_CR0(0))=MT7996_ICAP_CR0_RING_TRIGGER;
	capture_started=1;RES->first_cr9=R32(MT7996_ICAP_CR9(0));
	start=rdcycle64();last=rdcycle();R32(NCO_REG(N_STATUS))=N_OK;R32(NCO_REG(N_RUNNING))=1;
	for(;;) {
		uint32_t progress=0,didx=R32(TXDMA);
		uint64_t lag;
		R32(NCO_REG(N_NOW))=rdcycle();
		if(R32(GO_ADDR)==STOP_MAGIC)stopping=1;
		written=icap_written(&lap,&prev);lag=written-consumed*CHUNK;
		if((!stopping || moving!=NONE) && lag>=RING-SAFETY){status=3;break;}
		if(lag>max_lag)max_lag=(uint32_t)lag;
		while(read!=didx) {
			uint32_t id=owners[read];
			if(state[id]!=5){status=14;goto drain;}
			state[id]=0;read=(read+1)&7;completed++;progress=1;
		}
		if(moving!=NONE) {
			uint32_t nt=(txi+1)&127,nr=(rxi+1)%6;
			if(R32(D+0x30c)==nt && R32(D+0x50c)==nr && (R32(DSP_RX+16*rxi+4)&0x80000000u)) {
				if((R32(DSP_RX+16*rxi+4)&0xbfff0000u)!=0xa0000000u){status=12;break;}
				R32(DSP_RX+16*rxi+4)=CHUNK<<16;fence();R32(D+0x508)=rxi;
				state[moving]=2;moving=NONE;txi=nt;rxi=nr;consumed++;progress=1;
			}
		}
		for(uint32_t b=0;b<3;b++) {
			uint32_t c=DSP_CTL(b),id=active[b];
			if(id!=NONE && R32(c+48)){status=11;goto drain;}
			if(id!=NONE && R32(c+12)==ticket[b]) {
				if(R32(c+40)!=phase_at[id]+2048u*step_at[id] || R32(c+44)==NONE){status=11;goto drain;}
				clips+=R32(c+44);state[id]=4;active[b]=NONE;progress=1;
			}
			id=b+3*bank[b];
			if(active[b]==NONE && state[id]==2) {
				R32(c+16)=1;R32(c+20)=2048;R32(c+24)=phase_at[id];R32(c+28)=step_at[id];R32(c+32)=1;
				R32(c+80)=bank[b];ticket[b]++;fence();R32(c+8)=ticket[b];
				state[id]=3;active[b]=id;bank[b]^=1;progress=1;
			}
		}
		if(state[out_id]==4 && ((cpu+1)&7)!=read) {
			R32(slots+4*cpu)=0;R32(desc+16*cpu)=DSP_PACK(out_id%3,out_id/3);
			R16(desc+16*cpu+6)=0x4000|PACKED;owners[cpu]=out_id;state[out_id]=5;
			fence();cpu=(cpu+1)&7;R32(TXCPU)=cpu;out_id=(out_id+1)%6;published++;progress=1;
		}
		if(!stopping && moving==NONE && state[in_id]==0 && written>=(assigned+1)*CHUNK) {
			uint32_t req=R32(NCO_REG(N_REQ));
			if(req!=R32(NCO_REG(N_ACK))) {
				uint32_t result=N_STALE_RUN;
				if((int32_t)(R32(NCO_REG(N_DEADLINE))-rdcycle())<=0)result=N_EXPIRED;
				else if(R32(NCO_REG(N_TARGET_RUN))==run) {
					step=R32(NCO_REG(N_STEP));result=N_OK;
					R32(NCO_REG(N_APPLIED_STEP))=step;
					R32(NCO_REG(N_BOUNDARY_LO))=(uint32_t)(assigned<<11);
					R32(NCO_REG(N_BOUNDARY_HI))=(uint32_t)(assigned>>21);
					R32(NCO_REG(N_PHASE))=phase;
				}
				R32(NCO_REG(N_STATUS))=result;fence();R32(NCO_REG(N_ACK))=req;
			}
			phase_at[in_id]=phase;step_at[in_id]=step;phase+=2048u*step;
			state[in_id]=1;moving=in_id;in_id=(in_id+1)%6;
			R32(DSP_TX+16*txi+4)=(CHUNK<<16)|0x40000000u;fence();R32(D+0x308)=(txi+1)&127;
			assigned++;progress=1;
			R32(NCO_REG(N_ASSIGNED_LO))=(uint32_t)assigned;R32(NCO_REG(N_ASSIGNED_HI))=assigned>>32;
		}
		if(stopping && completed==assigned){status=7;break;}
		ctl.chunk=(uint32_t)published;
		if(lag<MT7996_PROBE_CTL_LAG_CHUNKS*CHUNK)ctl_step(&ctl);
		if(progress)last=rdcycle();
		else if(rdcycle()-last>72000000u){status=13;break;}
	}
drain:
	R32(NCO_REG(N_RUNNING))=0;
	/* Drain memory readers before stopping ICAP or releasing its route. */
	if(mem_started) {
		g0=rdcycle();
		while(R32(D+0x208)&10)if(rdcycle()-g0>72000000u){stalled=1;status=12;break;}
		if(!stalled)R32(D+0x208)=cfg&~5u;
	}
	g0=rdcycle();
	while(R32(TXDMA)!=cpu)if(rdcycle()-g0>72000000u){stalled=1;status=6;break;}
	if(!stalled) {
		RES->wa_status=DSP_QUIESCED;
		if(capture_started)R32(MT7996_ICAP_CR0(0))=MT7996_ICAP_CR0_IDLE;
		R32(ADAPTER+0x2c)=cpu;R32(ADAPTER+0x34)+=(uint32_t)published;
		if(published>=8)R32(ADAPTER+0x38)+=(uint32_t)published-8;
		reclaim();
	}
	RES->cycles=start?rdcycle64()-start:0;RES->chunks=published;RES->written=written;RES->max_lag=max_lag;
	RES->cpu_end=cpu;RES->tx_dma_end=R32(TXDMA);RES->cr9_end=R32(MT7996_ICAP_CR9(0));
	RES->cr0_end=R32(MT7996_ICAP_CR0(0));RES->sent_after=R32(ADAPTER+0x34);RES->done_after=R32(ADAPTER+0x38);
	RES->err_status=R32(MT7996_MCU_DMA0_INT_STA);RES->ctl_served=start?ctl.served:0;
	R32(NCO_REG(N_DMA_ERROR))=R32(D+0x110);R32(NCO_REG(N_CLIPS_LO))=(uint32_t)clips;R32(NCO_REG(N_CLIPS_HI))=clips>>32;
finish:
	R32(NCO_REG(N_RUNNING))=0;
	if(R32(NCO_REG(N_REQ))!=R32(NCO_REG(N_ACK))) {
		R32(NCO_REG(N_STATUS))=N_STOPPED;fence();R32(NCO_REG(N_ACK))=R32(NCO_REG(N_REQ));
	}
	RES->status=status;R32(DONE_AT)=DONE_MAGIC;R32(DONE_AT+4)=status;
}
