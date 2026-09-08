这是一个为你定制的 `start_guide.md`，基于你提供的操作流程整理而成。你可以将此文件保存在项目文件夹中，方便日后查阅。

### Start Guide: Faster Whisper 批量转译

这份指南记录了在 Windows 本地环境下使用 `faster-whisper` 进行批量音频转译的启动流程。

---

## 详细步骤 (Detailed Steps)

### 第一步：打开终端

1. 按 `Win + R`，输入 `powershell` 并回车，或者直接在开始菜单搜索 "PowerShell"。
2. 确保你当前的路径不影响 Conda 激活（通常默认即可）。

### 第二步：激活虚拟环境

使用预先配置好的 Conda 环境 `faster-whisper-env`：

```bash
conda activate faster-whisper-env

```

*成功标志：命令行最前方出现 `(faster-whisper-env)` 字样。*

### 第三步：执行转译脚本

运行 Python 脚本并指定包含音频/视频文件的**目标文件夹路径**。

**命令格式：**
`python [脚本路径] "[目标文件夹路径]"`

**示例（处理 Econ 1B Lecture 3）：**

```bash
python C:\Users\jason\Desktop\转译\fast_whisper_transcribe.py "C:\Users\jason\Downloads\econ 1B\lecture3"

```

运行后会显示格式菜单，用 **↑ / ↓（或 ← / →）切换，回车确认**，不需要打字输入选项。按 Esc 可取消。

- **SRT 字幕**：按单词时间戳切成短字幕，每条最多两行，英文每行目标不超过 42 字符，每条最长目标 6 秒；优先在标点及至少 0.5 秒的停顿处切分。
- **TXT 阅读文本**：保留按完整英文句子换行的排版。
- **SRT + TXT**：一次识别同时生成两个文件。

字幕不会强行延长到 2 秒，以免跨越停顿；特别长的单词不会拆开，因此单个超长词可能超过行宽或时长目标。时间精度取决于模型提供的单词时间戳。

也可以通过 `--format srt`、`--format txt` 或 `--format both` 跳过菜单，适用于自动化或非交互终端。

如果目标目录里已经有同名输出文件，脚本默认会跳过该格式。选择同时生成时，只生成缺少的格式。需要重新生成已有字幕时，加上 `--overwrite`（同时生成时会覆盖两种格式）：

```bash
python C:\Users\jason\Desktop\转译\fast_whisper_transcribe.py "C:\Users\jason\Downloads\econ 1B\lecture3" --overwrite
```

脚本默认使用 `batch_size=8` 的批量推理，以便更充分利用高性能显卡。RTX 5070 Ti
显存充足时可以尝试：

```bash
python C:\Users\jason\Desktop\转译\fast_whisper_transcribe.py "C:\Users\jason\Downloads\econ 1B\lecture3" --batch-size 16 --overwrite
```

如果出现显存不足，就改回 `--batch-size 8`。批量模式会按 VAD 语音片段并行处理，
通常不会明显影响识别质量，但结果不保证与原来的逐段模式逐字完全一致。需要复现原来的
非批量运行方式以及原有的长静音幻觉抑制逻辑时，使用 `--batch-size 0`。

`--beam-size` 默认仍为 5。将它设成 1 会进一步加速，但可能略微降低识别准确率：

```bash
python C:\Users\jason\Desktop\转译\fast_whisper_transcribe.py "C:\Users\jason\Downloads\econ 1B\lecture3" --batch-size 16 --beam-size 1 --overwrite
```
