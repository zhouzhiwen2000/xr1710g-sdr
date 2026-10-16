/* SPDX-License-Identifier: MIT */
#ifndef STREAM_DSP_NCO_H
#define STREAM_DSP_NCO_H

/* Host/WM control page, separate from stream results and the RF interpreter.
 * Publish TARGET_RUN and STEP, then REQ. ACK is written last. Commands belong
 * to one stream run and are consumed only at the next input-block boundary.
 */
#define NCO_BASE        0x0090f100u
#define NCO_MAGIC      0x4e434f32u
#define NCO_REG(n)     (NCO_BASE + 4u * (n))
enum { N_MAGIC, N_RUN, N_REQ, N_ACK, N_TARGET_RUN, N_STEP, N_APPLIED_STEP,
       N_BOUNDARY_LO, N_BOUNDARY_HI, N_PHASE, N_STATUS, N_RUNNING,
       N_CLIPS_LO, N_CLIPS_HI, N_DMA_ERROR, N_ASSIGNED_LO, N_ASSIGNED_HI,
       N_NOW, N_DEADLINE };
enum { N_OK, N_STALE_RUN, N_STOPPED, N_EXPIRED };
#define DSP_CTL(b)     (0x86205f00u + (b) * 0x400000u)
#define DSP_IN(b,s)    (0x86206000u + (b) * 0x400000u + (s) * 0x2000u)
#define DSP_PACK(b,s)  (0x8620a000u + (b) * 0x400000u + (s) * 0x2000u)
#define DSP_TX        0x0090b000u /* 128 immutable source descriptors, 2048 bytes */
#define DSP_RX        0x0090b800u /* six immutable destination descriptors */
#define DSP_QUIESCED  (1u << 9)
#endif
