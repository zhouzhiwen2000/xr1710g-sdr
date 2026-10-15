/* SPDX-License-Identifier: MIT
 * Standalone AArch64 NEON NCO feasibility probe. Uses private buffers only.
 * Build: aarch64-linux-gnu-gcc -O3 -mcpu=cortex-a53 -static \
 *          tools/router_nco_bench.c -lm -o router-nco-bench
 * Run: router-nco-bench CPU KERNEL SAMPLES SECONDS
 * KERNEL: full (continuous oscillator + SC16 rotation), table (rotation only).
 * SAMPLES must be a multiple of 2048. Rates are complex samples/second.
 * Excludes DMA ownership/cache synchronization, packed IQ and network export.
 */
#define _GNU_SOURCE
#include <arm_neon.h>
#include <errno.h>
#include <math.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define BLOCK 2048
#define TAU 6.283185307179586476925286766559

struct oscillator {
    double phase, omega;
    float base_re[8], base_im[8], step_re, step_im;
};

static double seconds(clockid_t clock)
{
    struct timespec t;
    clock_gettime(clock, &t);
    return t.tv_sec + t.tv_nsec * 1e-9;
}

static void init_osc(struct oscillator *o, double hz, double fs)
{
    o->phase = 0.123456789;
    o->omega = TAU * hz / fs;
    for (int j = 0; j < 8; ++j) {
        o->base_re[j] = cos(o->omega * j);
        o->base_im[j] = sin(o->omega * j);
    }
    o->step_re = cos(o->omega * 8);
    o->step_im = sin(o->omega * 8);
}

static int16x8_t narrow14(int32x4_t lo, int32x4_t hi)
{
    int16x8_t x = vcombine_s16(vqmovn_s32(lo), vqmovn_s32(hi));
    return vmaxq_s16(vdupq_n_s16(-8192), vminq_s16(vdupq_n_s16(8191), x));
}

__attribute__((noinline))
static void mix_full(const int16_t *restrict in, int16_t *restrict out,
                     size_t count, struct oscillator *o)
{
    for (size_t b = 0; b < count; b += BLOCK) {
        float c = cos(o->phase), s = sin(o->phase);
        float32x4_t br0 = vld1q_f32(o->base_re), bi0 = vld1q_f32(o->base_im);
        float32x4_t br1 = vld1q_f32(o->base_re + 4), bi1 = vld1q_f32(o->base_im + 4);
        float32x4_t cr0 = vsubq_f32(vmulq_n_f32(br0, c), vmulq_n_f32(bi0, s));
        float32x4_t ci0 = vaddq_f32(vmulq_n_f32(bi0, c), vmulq_n_f32(br0, s));
        float32x4_t cr1 = vsubq_f32(vmulq_n_f32(br1, c), vmulq_n_f32(bi1, s));
        float32x4_t ci1 = vaddq_f32(vmulq_n_f32(bi1, c), vmulq_n_f32(br1, s));
        for (size_t j = b; j < b + BLOCK; j += 8) {
            int16x8x2_t x = vld2q_s16(in + 2 * j), y;
            float32x4_t r0 = vcvtq_f32_s32(vmovl_s16(vget_low_s16(x.val[0])));
            float32x4_t i0 = vcvtq_f32_s32(vmovl_s16(vget_low_s16(x.val[1])));
            float32x4_t r1 = vcvtq_f32_s32(vmovl_high_s16(x.val[0]));
            float32x4_t i1 = vcvtq_f32_s32(vmovl_high_s16(x.val[1]));
            y.val[0] = narrow14(vcvtnq_s32_f32(vsubq_f32(vmulq_f32(r0, cr0), vmulq_f32(i0, ci0))),
                               vcvtnq_s32_f32(vsubq_f32(vmulq_f32(r1, cr1), vmulq_f32(i1, ci1))));
            y.val[1] = narrow14(vcvtnq_s32_f32(vaddq_f32(vmulq_f32(i0, cr0), vmulq_f32(r0, ci0))),
                               vcvtnq_s32_f32(vaddq_f32(vmulq_f32(i1, cr1), vmulq_f32(r1, ci1))));
            vst2q_s16(out + 2 * j, y);
            float32x4_t nr0 = vsubq_f32(vmulq_n_f32(cr0, o->step_re), vmulq_n_f32(ci0, o->step_im));
            float32x4_t nr1 = vsubq_f32(vmulq_n_f32(cr1, o->step_re), vmulq_n_f32(ci1, o->step_im));
            ci0 = vaddq_f32(vmulq_n_f32(ci0, o->step_re), vmulq_n_f32(cr0, o->step_im));
            ci1 = vaddq_f32(vmulq_n_f32(ci1, o->step_re), vmulq_n_f32(cr1, o->step_im));
            cr0 = nr0; cr1 = nr1;
        }
        o->phase = remainder(o->phase + BLOCK * o->omega, TAU);
    }
}

/* Optimistic bound: coefficients repeat every block; no oscillator update. */
__attribute__((noinline))
static void mix_table(const int16_t *restrict in, int16_t *restrict out,
                      size_t count, const int16_t *restrict coef)
{
    for (size_t b = 0; b < count; b += BLOCK) {
        for (size_t j = 0; j < BLOCK; j += 8) {
            int16x8x2_t x = vld2q_s16(in + 2 * (b + j));
            int16x8x2_t c = vld2q_s16(coef + 2 * j), y;
            int32x4_t r0 = vmlsl_s16(vmull_s16(vget_low_s16(x.val[0]), vget_low_s16(c.val[0])), vget_low_s16(x.val[1]), vget_low_s16(c.val[1]));
            int32x4_t r1 = vmlsl_high_s16(vmull_high_s16(x.val[0], c.val[0]), x.val[1], c.val[1]);
            int32x4_t i0 = vmlal_s16(vmull_s16(vget_low_s16(x.val[1]), vget_low_s16(c.val[0])), vget_low_s16(x.val[0]), vget_low_s16(c.val[1]));
            int32x4_t i1 = vmlal_high_s16(vmull_high_s16(x.val[1], c.val[0]), x.val[0], c.val[1]);
            y.val[0] = narrow14(vrshrq_n_s32(r0, 15), vrshrq_n_s32(r1, 15));
            y.val[1] = narrow14(vrshrq_n_s32(i0, 15), vrshrq_n_s32(i1, 15));
            vst2q_s16(out + 2 * (b + j), y);
        }
    }
}

static double clipped(double x) { return fmax(-8192, fmin(8191, x)); }

static int verify(const int16_t *in, int16_t *out, const int16_t *coef)
{
    double worst = 0;
    const double rates[] = {0, 25000, -1000000, 17345678.9, -19999999};
    for (size_t k = 0; k < sizeof(rates) / sizeof(rates[0]); ++k) {
        struct oscillator o;
        init_osc(&o, rates[k], 40000000);
        double initial = o.phase;
        /* Two consecutive calls also check phase continuity at block boundary. */
        for (int block = 0; block < 2; ++block) {
            mix_full(in, out, BLOCK, &o);
            for (int j = 0; j < BLOCK; ++j) {
                double a = initial + (j + block * BLOCK) * o.omega;
                double er = fabs(out[2*j] - clipped(in[2*j]*cos(a) - in[2*j+1]*sin(a)));
                double ei = fabs(out[2*j+1] - clipped(in[2*j+1]*cos(a) + in[2*j]*sin(a)));
                worst = fmax(worst, fmax(er, ei));
            }
        }
    }
    mix_table(in, out, BLOCK, coef);
    for (int j = 0; j < BLOCK; ++j) {
        double c = coef[2*j] / 32768., s = coef[2*j+1] / 32768.;
        worst = fmax(worst, fabs(out[2*j] - clipped(in[2*j]*c - in[2*j+1]*s)));
        worst = fmax(worst, fabs(out[2*j+1] - clipped(in[2*j+1]*c + in[2*j]*s)));
    }
    printf("{\"validation_max_error_lsb\":%.6f,\"passed\":%s}\n", worst, worst < 2 ? "true" : "false");
    return worst < 2;
}

int main(int argc, char **argv)
{
    if (argc != 5) return 2;
    int cpu = atoi(argv[1]), full = !strcmp(argv[2], "full");
    size_t n = strtoull(argv[3], NULL, 0);
    double duration = atof(argv[4]);
    if (cpu < 0 || cpu >= CPU_SETSIZE || (!full && strcmp(argv[2], "table")) ||
        n < BLOCK || n > (1u << 24) || n % BLOCK || !isfinite(duration) || duration <= 0 || duration > 5) return 2;
    cpu_set_t mask; CPU_ZERO(&mask); CPU_SET(cpu, &mask);
    if (sched_setaffinity(0, sizeof(mask), &mask)) { perror("affinity"); return 1; }
    int16_t *in = aligned_alloc(64, n*4), *out = aligned_alloc(64, n*4);
    int16_t *coef = aligned_alloc(64, BLOCK*4);
    if (!in || !out || !coef) return 1;
    uint32_t rng = 12345;
    for (size_t i = 0; i < n*2; ++i) { rng = rng*1664525 + 1013904223; in[i] = (rng >> 18) - 8192; }
    memset(out, 0, n*4);
    for (int j = 0; j < BLOCK; ++j) {
        coef[2*j] = lround(32767*cos(TAU*25000/320000000*j));
        coef[2*j+1] = lround(32767*sin(TAU*25000/320000000*j));
    }
    if (!verify(in, out, coef)) return 1;
    struct oscillator o; init_osc(&o, 25000, 320000000);
    size_t iterations = 0;
    double c0 = seconds(CLOCK_PROCESS_CPUTIME_ID), t0 = seconds(CLOCK_MONOTONIC), t1;
    do {
        if (full) mix_full(in, out, n, &o); else mix_table(in, out, n, coef);
        ++iterations;
        t1 = seconds(CLOCK_MONOTONIC);
    } while (t1 - t0 < duration);
    double elapsed = t1 - t0, cpu_s = seconds(CLOCK_PROCESS_CPUTIME_ID) - c0;
    int64_t sum = 0;
    for (size_t i = 0; i < n*2; ++i) sum += out[i];
    printf("{\"kernel\":\"%s\",\"cpu\":%d,\"samples\":%zu,\"iterations\":%zu,\"wall_s\":%.6f,\"cpu_s\":%.6f,\"wall_msps\":%.3f,\"cpu_msps\":%.3f,\"checksum\":%lld}\n",
           argv[2], cpu, n, iterations, elapsed, cpu_s, n*(double)iterations/elapsed/1e6, n*(double)iterations/cpu_s/1e6, (long long)sum);
    free(in); free(out); free(coef);
    return 0;
}
