#!/usr/bin/env python3
"""Render the XR1710G hardware/memory topology as a three-page vector PDF."""
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from matplotlib.font_manager import FontProperties
from matplotlib.backends.backend_pdf import PdfPages

OUT = Path(__file__).resolve().parent
FONT = FontProperties(fname='/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc')
BOLD = FontProperties(fname='/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',weight='bold')
# TrueType CJK outlines preserve vector rendering and searchable PDF text.
plt.rcParams.update({'pdf.fonttype': 42, 'svg.fonttype': 'path'})
INK, MUTED = '#172B44', '#53667C'
BLUE, TEAL, PURPLE, ORANGE = '#2167B0', '#147D72', '#7753B3', '#A5611B'
BG, LINE = '#F5F8FC', '#D9E2EC'

def page(title, sub, number):
    fig = plt.figure(figsize=(16.54, 11.69), facecolor='white')
    ax = fig.add_axes([.025, .03, .95, .94])
    ax.set(xlim=(0, 100), ylim=(0, 70)); ax.axis('off')
    text(ax, 2, 67, title, 25, bold=True)
    text(ax, 2, 63.4, sub, 12, color=MUTED)
    ax.plot([2, 98], [61, 61], color=LINE, lw=1)
    text(ax, 2, .8, 'XR1710G · 接收架构与芯片内存归属 · 硬件核对 2026-10-16 · 图文更新 2026-10-17', 9, color=MUTED)
    text(ax, 98, .8, f'{number} / 3', 9, ha='right', color=MUTED)
    return fig, ax

def text(ax, x, y, s, size=12, color=INK, ha='left', bold=False, va='center'):
    return ax.text(x, y, s, fontproperties=BOLD if bold else FONT,
                   fontsize=size, color=color, ha=ha, va=va, linespacing=1.55, zorder=5)

def box(ax, x, y, w, h, title, body='', color=BLUE, fill='white', fs=13, bs=11, dashed=False):
    p = FancyBboxPatch((x,y),w,h,boxstyle='round,pad=0.0,rounding_size=0.7',
                       edgecolor=color,facecolor=fill,linewidth=1.4,
                       linestyle=(0,(4,3)) if dashed else '-',zorder=2)
    ax.add_patch(p)
    if body:
        text(ax,x+w/2,y+h*.72,title,fs,color=color,ha='center',bold=True)
        text(ax,x+w/2,y+h*.30,body,bs,ha='center')
    else:
        text(ax,x+w/2,y+h/2,title,fs,color=color,ha='center',bold=True)
    return p

def panel(ax,x,y,w,h,title,sub,color):
    p=FancyBboxPatch((x,y),w,h,boxstyle='round,pad=0.0,rounding_size=1.0',
                    edgecolor=color,facecolor=BG,linewidth=1.7,zorder=0)
    ax.add_patch(p)
    text(ax,x+1.4,y+h-2.1,title,17,color=color,bold=True)
    text(ax,x+1.4,y+h-4.6,sub,10.5,color=MUTED)

def arrow(ax,pts,color=BLUE,both=False,dashed=False,lw=1.8):
    # Orthogonal polylines with an arrowhead on the final segment.
    style=(0,(4,3)) if dashed else '-'
    if len(pts)>2:
        ax.plot(*zip(*pts[:-1]),color=color,lw=lw,ls=style,zorder=3)
    ax.add_patch(FancyArrowPatch(pts[-2],pts[-1],arrowstyle='<->' if both and len(pts)==2 else '-|>',
                 mutation_scale=13,color=color,linewidth=lw,linestyle=style,zorder=3,
                 shrinkA=1,shrinkB=1))

def main():
    with PdfPages(OUT/'xr1710g-hardware-topology.pdf',metadata={
        'Title':'XR1710G 芯片、内存与 DMA 拓扑',
        'Subject':'AN7581 / MT7996 / MT7976 / MT7977; verified receiver topology',
        'Author':'XR1710G SDR project'}) as pdf:
        fig,ax=page('先分清三层：射频前端、无线处理器、Linux 主 SoC',
            'DSP / MEM_DMA / ICAP 在 MT7996；8 核网络 NPU 在 AN7581；MT7977 负责射频。',1)
        panel(ax,2,25,25,33,'射频前端 A-die','天线与 FEM → LNA / 混频 / LO / 模拟滤波',ORANGE)
        box(ax,4,45,21,6,'MT7976','band 0 · 2.4 GHz',ORANGE,fs=14)
        box(ax,4,36,21,6,'MT7977 × 1','band 1 · 5 GHz',ORANGE,fs=14)
        box(ax,4,27,21,6,'MT7977 × 1','band 2 · 6 GHz',ORANGE,fs=14)
        arrow(ax,[(25,48),(35,48)],ORANGE)
        arrow(ax,[(25,39),(30,39),(30,46),(35,46)],ORANGE)
        arrow(ax,[(25,30),(32,30),(32,44),(35,44)],ORANGE)
        text(ax,31,52.2,'各 band 独立\n模拟 I/Q',9,color=ORANGE,ha='center')
        text(ax,14.5,22.4,'射频控制：MT7996 WM → SPI → A-die',10,color=MUTED,ha='center')

        panel(ax,35,25,30,33,'MT7996 无线处理器','数字基带 / 无线控制 / 芯片内部存储',BLUE)
        box(ax,37,44,26,7,'AFE / ADC → PHY / DFE → ICAP','ICAP0 把采样写入片上缓冲池',BLUE,fs=12,bs=10.5)
        box(ax,37,35,8.2,6.7,'WM','无线主控\nRISC-V · 720 MHz',BLUE,fs=13,bs=8.5)
        box(ax,45.9,35,8.2,6.7,'WA','无线加速\nRISC-V · 720 MHz',BLUE,fs=13,bs=8.5)
        box(ax,54.6,35,8.2,6.7,'3 × DSP','信号处理\n各有 ITCM / DTCM',PURPLE,fs=13,bs=8.5)
        box(ax,37,27,26,5.8,'片上 SRAM + DMA','ICAP 池、MEM_DMA、外发 MCU_DMA / WFDMA',TEAL,fs=13,bs=9)

        panel(ax,73,25,25,33,'AN7581 主 SoC','运行 Linux，管理 PCIe 和以太网',TEAL)
        box(ax,75,44,21,7,'4 × Cortex-A53','Linux / w1700k_sdr 驱动',TEAL,fs=14,bs=11)
        box(ax,75,35,21,7,'8 × RISC-V NPU','网络处理器；当前 SDR 配置禁用',MUTED,fs=13,bs=10,dashed=True)
        box(ax,75,27,21,6,'PCIe / DDR 控制器 / Ethernet','QDMA / MAC 负责网口收发',TEAL,fs=11.5,bs=10)
        arrow(ax,[(65,46.3),(73,46.3)],BLUE,both=True)
        text(ax,69,49.4,'主 HIF',9.5,ha='center',color=BLUE)
        text(ax,69,43.7,'Gen3 ×2',9.5,ha='center',color=BLUE)
        arrow(ax,[(65,36),(73,36)],MUTED,both=True,dashed=True)
        text(ax,69,39,'第二 HIF',9.3,ha='center',color=MUTED)
        text(ax,69,33.5,'Gen2 ×1',9.3,ha='center',color=MUTED)
        text(ax,69,30.9,'IQ 未使用',8.8,ha='center',color=MUTED)

        box(ax,73,8,25,10,'外接 DDR4 · 2 GiB','连接 AN7581 的 DDR 控制器\n启动日志：PCDDR4，2666 MT/s',TEAL,fs=16,bs=11)
        arrow(ax,[(91,25),(91,18)],TEAL,both=True)
        text(ax,92,21.5,'DDR 总线',10,color=TEAL)
        box(ax,35,8,30,10,'外部 Linux 主机','10GbE 接收 IQ → UE 解调 / 感知 / viewer',BLUE,fs=16,bs=10.5)
        arrow(ax,[(80,25),(80,21),(50,21),(50,18)],BLUE)
        text(ax,58,22.8,'10GbE / UDP',11,color=BLUE)
        text(ax,3,16.8,'内存归属的关键',13,bold=True)
        text(ax,3,12.6,'MT7996：片上 SRAM / TCM\nAN7581：外接 2 GiB DDR4\n未发现无线芯片另配独立 DDR',11)
        text(ax,3,4.5,'实线：已有接收数据通路；灰虚线：当前 IQ 路径未使用。图示为启流后的结构，各大框表示芯片/功能归属，不代表 PCB 布局。',10,color=MUTED)
        pdf.savefig(fig)
        fig.savefig(OUT/'xr1710g-hardware-topology-1.png',dpi=145)
        plt.close(fig)

        fig,ax=page('两级缓冲：MT7996 片上 SRAM → PCIe → AN7581 DDR4',
            '默认 IQ 外发经过 PCIe；MEM_DMA 在 MT7996 内搬运数据，供 DSP 处理。',2)
        panel(ax,2,23,63,35,'MT7996 内部','蓝色：默认 IQ 导出路径    紫色：独立 DSP 本地搬运验证',BLUE)
        box(ax,4,45,13,6,'ICAP0','接收采样写入',BLUE,fs=13,bs=10)
        box(ax,22,45,23,6,'ICAP 环形缓冲 · 1 MiB','UMAC 共享 SRAM 池内的一部分',TEAL,fs=13,bs=9.5)
        box(ax,50,45,13,6,'MCU_DMA0','→ WFDMA / HIF',BLUE,fs=12.5,bs=9)
        arrow(ax,[(17,48),(22,48)],BLUE)
        arrow(ax,[(45,48),(50,48)],BLUE)
        text(ax,47.5,52,'WM / WA 调度，可打包',9,ha='center',color=MUTED)

        box(ax,22,35,23,6,'MEM_DMA · TX0 → RX0','片内 DMA，基址 0x58000000',PURPLE,fs=13,bs=10)
        arrow(ax,[(33.5,45),(33.5,41)],PURPLE,both=True)
        text(ax,4,39.5,'已验证 8 KiB 块：\n池 → DSP 约 0.79 GB/s\nDSP → 池约 0.80 GB/s',10,color=PURPLE)
        for x,b in ((4,0),(24.5,1),(45,2)):
            box(ax,x,25,18,6,f'DSP{b} · 640 MHz','64 KiB ITCM + 64 KiB DTCM',PURPLE,fs=12.5,bs=9)
        # The branch is a shared engine, not three independent bandwidths.
        ax.plot([13,54],[33,33],color=PURPLE,lw=1.6)
        arrow(ax,[(33.5,35),(33.5,33)],PURPLE,both=True)
        for x in (13,33.5,54): arrow(ax,[(x,33),(x,31)],PURPLE,both=True)
        text(ax,54,38,'DSP 用本地 LOAD / STORE\n处理 DTCM 中的数据',10,ha='center',color=PURPLE)

        panel(ax,73,23,25,35,'AN7581 + 外接 DDR4','另一个芯片的地址空间，经 PCIe DMA 到达',TEAL)
        box(ax,75,45,21,6,'DDR 中的 IQ 环 · 1 GiB','属于板上总计 2 GiB 的 DDR4',TEAL,fs=13,bs=10)
        box(ax,75,35,21,6,'Linux 驱动 / ARM CPU','管理描述符与 UDP，附加原数据页',TEAL,fs=12,bs=10)
        box(ax,75,25,21,6,'Ethernet QDMA / MAC','DMA 读 DDR → 10GbE → Linux 主机',TEAL,fs=12,bs=9.5)
        arrow(ax,[(65,48),(75,48)],BLUE)
        # Connect chip-edge to the DMA producer box.
        ax.plot([63,65],[48,48],color=BLUE,lw=1.8)
        text(ax,69,51.3,'PCIe',11,ha='center',color=BLUE)
        text(ax,69,44.8,'Gen3 ×2',9.7,ha='center',color=BLUE)
        arrow(ax,[(85.5,45),(85.5,41)],BLUE)
        arrow(ax,[(85.5,35),(85.5,31)],BLUE)
        text(ax,69,36.5,'跨芯片\n数据搬运',10,ha='center',color=MUTED)

        text(ax,3,20.2,'容量不能混为一谈',13,bold=True)
        # Simple ownership table: explanatory rather than an exhaustive memory map.
        rows=[
            ('MT7976 / MT7977','模拟射频前端','没有本项目可用的 IQ DDR 缓冲'),
            ('MT7996','片上 SRAM / TCM','UMAC 池共 1408 KiB；其中 1024 KiB 给 ICAP；每个 DSP 自有 64 KiB DTCM'),
            ('AN7581','外接 DDR4，2 GiB','ARM、DMA 与可选网络 NPU 共用；当前 IQ 环使用其中 1 GiB'),
        ]
        for i,(a,b,c) in enumerate(rows):
            y=16.7-i*3.2
            ax.plot([3,97],[y-1.6,y-1.6],color=LINE,lw=.7)
            text(ax,3,y,a,11,bold=True); text(ax,23,y,b,11); text(ax,45,y,c,10.3)
        text(ax,3,6.1,'可选 DSP NCO 已支持 40/80 MS/s 连续接收；完整路径见 docs/dsp-nco.md。图中 DMA 速率为独立短测，不能按三核相加。',10,color=MUTED)
        text(ax,3,3.7,'依据：本机启动日志 / PCIe 状态、仓库 hw 寄存器定义、2026-10-16 DSP DMA 实测；完整出处见同目录 sources.md。',9.5,color=MUTED)
        pdf.savefig(fig)
        fig.savefig(OUT/'xr1710g-hardware-topology-2.png',dpi=145)
        plt.close(fig)

        fig,ax=page('WM / WA 做什么？MT7996 的片上 RAM 有多少？',
            'WM、WA 是两个可编程的 RISC-V MCU；按已识别区域统计，RAM 共 5744 KiB ≈ 5.61 MiB（含 TCM，不含 ROM / cache）。',3)
        panel(ax,2,38,46,20,'WM · 无线主控核','Wireless Main · Andes RV32 / A45 类 · 720 MHz',BLUE)
        text(ax,4,48.8,'默认路径中的任务',12,color=BLUE,bold=True)
        text(ax,4,43.6,'射频初始化、调频 / 增益 / 滤波控制；跟踪 ICAP 写指针；\n发布 DMA 描述符并参与打包；可选 NCO 模式调度 DSP。',12)
        panel(ax,52,38,46,20,'WA · 无线加速核','Wireless Accelerator · Andes RV32 / A45 类 · 720 MHz',TEAL)
        text(ax,54,48.8,'默认路径中的任务',12,color=TEAL,bold=True)
        text(ax,54,43.6,'按 WM 分配的任务，无损压紧 I/Q 有效的 14-bit 数据；\n与 WM 共同完成高采样率模式的打包，降低网口带宽。',12)
        text(ax,3,34.7,'这些核在 MT7996 内，与 AN7581 的 ARM / 网络 NPU 分属不同芯片。三个 DSP 另有自己的本地 TCM。',11,color=MUTED)
        text(ax,3,30.9,'内存区域',12,bold=True)
        text(ax,29,30.9,'容量',12,bold=True)
        text(ax,45,30.9,'组成 / 当前用途',12,bold=True)
        ax.plot([3,97],[29.3,29.3],color=LINE,lw=1)
        rows=[
            ('共享 CRAM','2544 KiB','CRAM0 1408 + CRAM1 1136；WM / WA 的程序和数据'),
            ('UMAC 共享池','1408 KiB','ICAP 1024 + PLE 128 + PSE 256；采样缓冲来自这里'),
            ('WM 本地 / 系统 RAM','896 KiB','ULM1 224 + ULM2 256 + SYSRAM 416'),
            ('WA 本地 RAM','512 KiB','ILM 112（指令）+ DLM 400（数据）'),
            ('3 个 DSP 的 TCM','384 KiB','3 ×（64 KiB ITCM + 64 KiB DTCM）'),
            ('已识别区域合计','5744 KiB','约 5.61 MiB；地址别名及子分区没有重复计数'),
        ]
        for i,(a,b,c) in enumerate(rows):
            y=27.4-i*3.4
            ax.plot([3,97],[y-1.7,y-1.7],color=LINE,lw=.7)
            text(ax,3,y,a,12,bold=i==5)
            text(ax,29,y,b,12,color=TEAL if i==5 else INK,bold=i==5)
            text(ax,45,y,c,11)
        text(ax,3,6.5,'这是已识别内存区的容量统计，并非官方完整 SRAM 总量，也不是空闲容量；当前连续 IQ 采样池仍为其中的 1 MiB。',10.5,color=MUTED)
        text(ax,3,4.2,'另有 WM / WA cache 合计 128 KiB，未计入上表。容量依据 mcu_map.h / dsp.h，核与 cache 依据 mcu_periph.h；出处见 sources.md。',10,color=MUTED)
        pdf.savefig(fig)
        fig.savefig(OUT/'xr1710g-hardware-topology-3.png',dpi=145)
        plt.close(fig)

if __name__=='__main__':
    main()
