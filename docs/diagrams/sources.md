# XR1710G 芯片与内存拓扑：说明和依据

[三页 PDF](xr1710g-hardware-topology.pdf) · [芯片连接 PNG](xr1710g-hardware-topology-1.png) · [内存与 DMA PNG](xr1710g-hardware-topology-2.png) · [WM/WA 与 RAM 容量 PNG](xr1710g-hardware-topology-3.png)

图示针对这台 XR1710G 的现有 SDR 接收架构。第一张是功能连接图，不是 PCB 布线或封装引脚图；第二张展示启流后的默认数据流和独立 DMA 验证支路。硬件拓扑于 2026-10-16 核对，图文于 2026-10-17 更新；可选的连续 [DSP NCO 接收路径](../dsp-nco.md)单独说明。

## 芯片分工

- **MT7976 / 两颗 MT7977** 是射频 A-die，分别对应 2.4 / 5 / 6 GHz 频段。包含 LO/PLL、混频和模拟滤波等功能；每个频段有独立的模拟 I/Q 通路到 MT7996。图中省略各天线链、FEM、滤波器与时钟连线。
- **MT7996** 是通过 PCIe 连接主 SoC 的无线处理器。ICAP、UMAC SRAM、WM/WA RISC-V MCU、三个 DSP，以及本次验证的 MEM_DMA 均属于它。三个 DSP 每核有自己的 ITCM 和 DTCM。
- **AN7581** 是运行 Linux 的主 SoC，包含四核 Cortex-A53、八核 RISC-V 网络 NPU、PCIe/DDR 控制器及以太网硬件。当前设备树禁用了 NPU；QDMA/MAC 仍负责网口 DMA 收发。NPU 的全称在这里是网络处理器，不是神经网络推理加速器。

AN7581 的四核 ARM 与八核 RISC-V 网络处理器配置可在 [Airoha 官方产品页](https://www.airoha.com/products/p/NpfqGLnZiZuYUZ6N) 核对。当前板级连接以本机日志、设备树和本项目寄存器定义为准。

## WM 和 WA

图中 WM 表示无线主控核，WA 表示无线加速核。英文命名采用研究者对 MediaTek Wi-Fi 固件的说明：Wireless Main / Wireless Accelerator，参见 [Nullcon 2025 研究报告第 10 页](https://nullcon.net/wp-content/uploads/2026/04/Unlock-hidden-Superpowers-in-MediaTek-WiFi-Chips.pdf#page=10)。该页以 MT7981 为例；本图中的 MT7996 核型、内存及实际任务来自本项目对 MT7996 的表征与当前固件，不套用 MT7981 的处理器架构或内存容量。

MT7996 的 WM 和 WA 均为 Andes AndeStar V5、A45 类 RV32 核，当前固件设为 720 MHz；这里的 WA 本身是一颗运行固件的处理器。具体身份与 cache 定义见 [mcu_periph.h](../../include/hw/mt7996/mcu_periph.h)。

默认路径中，WM 执行无线初始化、射频控制、采样流调度、DMA 描述符发布及部分 IQ 打包；WA 执行 WM 分配的 IQ 打包工作。半块打包模式每 16 块由 WM 打包 4 块、WA 打包 4 块，其余 8 块保持原格式，平均每个复采样 30 bit。这些是[本项目固件](../../firmware/README.md)的具体分工，并非两颗核只能执行这些任务。可选 DSP NCO 模式由 WM 调度三颗 DSP 完成旋转和打包，外发 DMA 直接读取 DSP DTCM，不启动 WA 打包器。

## 内存归属

已确认板上 **2 GiB 外接 DDR4 连接 AN7581**。原厂启动日志输出 `dram_type = PCDDR4, data_rate = 2666`，U-Boot 输出 `DRAM: 2 GiB`。Linux 的可用内存低于该容量，是由于固件和内核保留区；不能把 `/proc/meminfo` 的 MemTotal 当成板上物理容量。

无线侧已确认的工作存储是 **MT7996 片上 SRAM/TCM**，不能将其称为无线芯片的 DDR。UMAC 池总量为 1408 KiB；现有 SDR 布局把其中 1024 KiB 分配给 ICAP，其余用于 PLE/PSE。它不是 MT7996 所有 SRAM 的总量，图中还省略了 WM/WA 的 ULM、CRAM 等代码与数据存储。

每个 DSP 具有 64 KiB ITCM 和 64 KiB DTCM。DSP 的普通 LOAD/STORE 使用本地 DTCM；本次验证的片内 MEM_DMA 负责 DTCM 与 UMAC 池之间的数据交换。正常 IQ 外发则使用 MCU_DMA0/WFDMA，经 PCIe 写入 AN7581 DDR 中的 1 GiB 环。这个 1 GiB 环属于板上 2 GiB DDR 的一部分，并非另外一颗 1 GiB 内存。

按当前内存表中互不重叠的区域统计：

| 区域 | 容量 | 组成 |
| --- | ---: | --- |
| 共享 CRAM | 2544 KiB | CRAM0 1408 + CRAM1 1136 |
| UMAC 共享池 | 1408 KiB | 当前 ICAP 1024 + PLE 128 + PSE 256 |
| WM 本地/系统 RAM | 896 KiB | ULM1 224 + ULM2 256 + SYSRAM 416 |
| WA 本地 RAM | 512 KiB | ILM 112 + DLM 400 |
| 三个 DSP 的 TCM | 384 KiB | 3 ×（64 KiB ITCM + 64 KiB DTCM）|
| 合计 | **5744 KiB ≈ 5.61 MiB** | 不重复计算别名、子区域或固件镜像占用 |

这个数字是仓库已识别 RAM 地址区域的统计，不是官方公布的完整 SRAM 物理容量，也不是全部可自由分配的空间。本次没有新增全空间读写或物理去重测试。16 KiB 采样窗口已包含在 SYSRAM 内，ICAP 的 1 MiB 已包含在 UMAC 内，不能另加。另有 WM 的 32 KiB I-cache + 64 KiB D-cache，以及 WA 的 16 KiB I-cache + 16 KiB D-cache，共 128 KiB cache，未计入此 RAM 汇总；ROM 也不计入。

目前没有发现 MT7996/MT7977 另接独立 DDR 的证据，也没有本项目可用的这种内存接口。图中的 SRAM/TCM 与 DDR 区分来自已识别的地址空间和数据通路；本次没有拆板或取得完整原理图，不能据此宣称已穷尽所有未公开的芯片功能。

## 接口与验证范围

主 HIF 是 `14c3:7990`，本机实读 Gen3 ×2；第二 HIF 是同一 MT7996 的 `14c3:7991`，本机实读 Gen2 ×1。它们是两个 PCIe host interface，不是两颗独立 MT7996。当前 SDR 驱动仅使用主接口；第二接口尚未实现或验证任意 IQ 块分流。

DSP DMA 的约 0.79 / 0.80 GB/s 是 8 KiB 块、每组 128 次、单个未完成事务的短时单向结果。已完成三个 DSP 的双向拷贝、独立 WM 回读、保护区及 DSP 本地 XOR 交叉校验。图中的紫色箭头表示这项独立搬运验证，单核搬运速率不能简单按三核相加。后续实现的可选 DSP NCO 路径已通过 40/80 MS/s 连续接收与在线调频验证；其完整数据流、实测吞吐和适用范围见 [DSP NCO](../dsp-nco.md)。

## 原始依据

| 图中信息 | 依据 |
| --- | --- |
| 主 SoC、板级 PCIe 和 NPU 配置 | [XR1710G DTS](../../platform/xr1710g/an7581-xr1710g-sdr-ram.dts)，实机 `/sys/firmware/devicetree/base/soc/npu*/status=disabled` |
| 2 GiB DDR4、2666 MT/s | 本地 `runtime/uboot-install-20261016/automatic-linux-boot.log`，原厂 DRAMC 输出及 U-Boot 容量 |
| 射频 A-die 型号、频段与 SPI | [MT7977](../../include/hw/adie/mt7977.h)、[射频控制定义](../../include/hw/adie/adie.h) |
| AFE/ADC、PHY/DFE | [PHY 寄存器说明](../../include/hw/mt7996/phy.h)、[CONN AFE](../../include/hw/mt7996/conn.h) |
| UMAC 总池 1408 KiB、ICAP 分区 1 MiB | [MCU 内存图](../../include/hw/mt7996/mcu_map.h) |
| DSP 每核 64 KiB ITCM/DTCM、640 MHz | [DSP 定义](../../include/hw/mt7996/dsp.h)；容量/时钟来自仓库既有硬件表征，本次 DMA 验证只覆盖指定的最多 8 KiB 数据区 |
| MEM_DMA 与外发 MCU_DMA | [MCU DMA 定义](../../include/hw/mt7996/mcu_dma.h) |
| 本次 DSP 数据正确性及速度 | 本地 `runtime/dsp-dma-20261016/REPORT.md`、`pool-bench.json`、`live-dsp*.json`、`validation.json` |
| 两个 HIF 及速率 | [Host 接口定义](../../include/hw/mt7996/host.h)，本机 sysfs 实读；补充记录 `runtime/pcie-secondary-20261016/REPORT.md` |
| NPU 与 ARM 共用 DDR | [AN7581 NPU 表征](../../include/hw/an7581/npu.h)，[Airoha 官方规格](https://www.airoha.com/products/p/NpfqGLnZiZuYUZ6N) |
| 1 GiB DDR 接收环、10GbE UDP | [接收架构](../architecture.md)、[PCIe 驱动](../../kernel/w1700k_sdr/pci.c) |

原始串口记录保留在本机运行目录，图和本文不包含设备密钥、网络凭证或完整串口转储。

## 重绘

使用 Python 3、Matplotlib 和系统文泉驿正黑字体：

```bash
python3 docs/diagrams/draw_xr1710g_topology.py
```

输出三页 A3 横向矢量 PDF 和三个 PNG 预览。字体路径在脚本开头，可按绘图主机安装位置调整。
