# SPDX-License-Identifier: MIT
"""Exercise every-word comparison and capture-buffer bounds in host memory.

This test substitutes cache maintenance only; it does not validate MCU cache
behavior, which requires the hardware positive controls.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('cc'), 'native C compiler required')
class IcapMemoryBoundsTest(unittest.TestCase):
    def test_full_pool_edges_window_and_guards(self):
        source = r'''
#define _GNU_SOURCE
#define ICAP_PROBE_HOST_TEST
#include <sys/mman.h>
#include <assert.h>
#include <string.h>
#include "tools/icap_memory_probe.c"
static void region(uintptr_t a, size_t n) {
    void *p = mmap((void *)a,n,PROT_READ|PROT_WRITE,
                  MAP_PRIVATE|MAP_ANONYMOUS|MAP_FIXED_NOREPLACE,-1,0);
    assert(p == (void *)a);
    memset(p,0x5a,n);
}
int main(void) {
    region(0xe03ff000,0x102000);
    region(0x463000,0x6000);
    region(0x90d000,0x2000);
    assert(probe(0,1234)==260);
    assert(probe(1,1234)==260);
    volatile struct result *r=(volatile struct result *)OUTPUT;
    for(unsigned i=0;i<260;i++) assert(r[i].changed==0);
    *(volatile uint32_t *)0xe0400000=0;
    *(volatile uint32_t *)0xe04ffffc=0;
    *(volatile uint32_t *)0x467ffc=0;
    assert(probe(1,1234)==260);
    for(unsigned i=0;i<260;i++) assert(r[i].changed==(i==0||i==255||i==259));
    assert(r[0].first==0 && r[0].last==0);
    assert(r[255].first==4092 && r[255].last==4092);
    assert(r[259].first==4092 && r[259].last==4092);
    assert(probe(99,0)==0xffffffffu);
    assert(*(volatile uint32_t *)0xe03ffffc==0x5a5a5a5a);
    assert(*(volatile uint32_t *)0xe0500000==0x5a5a5a5a);
    assert(*(volatile uint32_t *)0x463ffc==0x5a5a5a5a);
    assert(*(volatile uint32_t *)0x468000==0x5a5a5a5a);
    assert(*(volatile uint32_t *)0x90e840==0x5a5a5a5a);
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path/'test.c').write_text(source)
            subprocess.run(['cc','-O2','-Wall','-Werror','-I',str(ROOT),'-I',str(ROOT/'include'),
                            str(path/'test.c'),'-o',str(path/'test')],check=True,capture_output=True)
            subprocess.run([str(path/'test')],check=True,capture_output=True,timeout=5)


if __name__ == '__main__':
    unittest.main()
