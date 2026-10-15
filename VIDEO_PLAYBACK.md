# 视频播放

在接收 UE 转发视频的播放电脑上打开终端，复制下面这一整行运行：

```bash
ffplay -fflags nobuffer -flags low_delay -framedrop -sync ext "udp://0.0.0.0:50001?fifo_size=65536&overrun_nonfatal=1&buffer_size=16777216"
```

出现视频画面并播放声音即表示接收成功。按 `q` 退出播放器。

发送端循环播放上传的 `video.mp4`，编码为 1080p、30 fps、H.264 视频和 AAC 音频。UE 将空口接收的视频转发到配置的播放电脑 **UDP 50001** 端口，视频编码目标 **30 Mb/s**，含音频和封装的总码流约 **33 Mb/s**。

若 Windows 弹出防火墙提示，请允许 ffplay 在当前局域网接收数据。

也可以在 VLC 中选择“媒体 → 打开网络串流”，输入：

```text
udp://@:50001
```

星座图和感知 viewer 连接 **运行 UE 的 Linux 主机的局域网 IP**，端口使用默认值。
播放电脑 IP 须与 UE 视频转发脚本的目标地址一致。

当前空口采样率为 **80 MS/s**，中心频率为 **2.350 GHz**，UE 使用 8 个解调线程。
