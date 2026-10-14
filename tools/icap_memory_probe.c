/* SPDX-License-Identifier: MIT */
/* Idle WM diagnostic, linked in the unused pack buffer. No configurable
 * pointers: only the reserved 1 MiB capture pool and 16 KiB capture window
 * are written. PLE/PSE, executable firmware and calibration are excluded. */
#include <stdint.h>
#include "hw/mt7996/iq_format.h"
#include "hw/mt7996/mcu_periph.h"

#define OUTPUT 0x0090d800u
#define PAGE_WORDS 1024u
#define PAGES 260u

struct result { uint32_t changed, first, last, hash; };

static void sync_cache(void)
{
#ifndef ICAP_PROBE_HOST_TEST
	asm volatile("csrwi %0, %1; fence" ::
		"n"(MT7996_MCU_CSR_MCCTLCOMMAND), "n"(MT7996_MCU_CCTL_L1D_WBINVAL_ALL) : "memory");
#endif
}

/* mode 0 fills every word; mode 1 compares every word and emits per-page
 * counts/first/last offsets and FNV-1a over words. mode 2 only synchronizes.
 * The high spare bits are always set in the sentinel, unlike raw IQ. */
__attribute__((section(".text.entry"))) uint32_t probe(uint32_t mode, uint32_t seed)
{
	volatile struct result *out = (volatile struct result *)OUTPUT;
	if (mode > 2)
		return 0xffffffffu;
	sync_cache();
	if (mode == 2)
		return 0;
	for (uint32_t page = 0; page < PAGES; page++) {
		uint32_t base = page < 256 ? MT7996_IQ_POOL_BASE + page * 4096 :
			MT7996_IQ_WIN_BASE + (page - 256) * 4096;
		volatile uint32_t *p = (volatile uint32_t *)(uintptr_t)base;
		uint32_t changed = 0, first = 0xffffffffu, last = 0xffffffffu, hash = 2166136261u;
		for (uint32_t i = 0; i < PAGE_WORDS; i++) {
			uint32_t expected = 0xc000c000u | ((seed ^ (page * PAGE_WORDS + i)) & 0x3fff3fffu);
			if (!mode) {
				p[i] = expected;
			} else {
				uint32_t value = p[i];
				hash = (hash ^ value) * 16777619u;
				if (value != expected) {
					if (!changed) first = i * 4;
					last = i * 4;
					changed++;
				}
			}
		}
		if (mode) {
			out[page].changed = changed;
			out[page].first = first;
			out[page].last = last;
			out[page].hash = hash;
		}
	}
	sync_cache();
	return PAGES;
}
