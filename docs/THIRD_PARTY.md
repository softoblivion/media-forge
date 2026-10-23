# Third-party components

| Component | License | How it ships |
|---|---|---|
| [CPython](https://www.python.org/) | PSF-2.0 | bundled into the exe |
| [pywebview](https://github.com/r0x0r/pywebview) | BSD-3-Clause | bundled into the exe |
| [pythonnet](https://github.com/pythonnet/pythonnet), clr_loader | MIT | bundled into the exe |
| bottle, proxy_tools | MIT | bundled into the exe |
| [NumPy](https://numpy.org/) | BSD-3-Clause | bundled into the exe |
| [Pillow](https://python-pillow.org/) | MIT-CMU | bundled into the exe |
| [pillow-heif](https://github.com/bigcat88/pillow_heif) with libheif | BSD-3-Clause, LGPL-3.0 | bundled into the exe |
| [OpenCV](https://opencv.org/) with the contrib modules | Apache-2.0 | bundled into the exe |
| [FSRCNN](https://github.com/Saafke/FSRCNN_Tensorflow) model files | Apache-2.0 | bundled into the exe |
| [PyInstaller](https://pyinstaller.org/) bootloader | GPL-2.0-or-later with the bootloader exception | bundled into the exe |
| [Unbounded](https://github.com/googlefonts/unbounded), [Geist Mono](https://github.com/vercel/geist-font) fonts | SIL Open Font License 1.1 | bundled into the exe |
| [FFmpeg](https://ffmpeg.org/) ([yt-dlp/FFmpeg-Builds](https://github.com/yt-dlp/FFmpeg-Builds)) | GPL | read from `PATH`; when none is there, downloaded at first start from its official release channel into `media-forge_data\bin\`, not redistributed with this project |
| [EDSR](https://github.com/Saafke/EDSR_Tensorflow) model files | Apache-2.0 | downloaded on first use from their official release channel into `media-forge_data\models\`, not redistributed with this project |
| Microsoft Edge WebView2 Runtime | Microsoft licence | part of Windows; not shipped |
