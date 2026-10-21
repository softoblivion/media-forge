# Super-resolution model files

Deterministic super-resolution networks for OpenCV `dnn_superres` (`cv2.dnn_superres.DnnSuperResImpl`). Each model
is a fixed stack of convolutions that maps a low-resolution patch to a high-resolution patch; the same input always
gives the same output. None of them is generative (no GAN, no diffusion, no prompt).

The SHA-256 values below are pinned in `core/upscale.py` (`MODEL_FILES`); a file whose hash differs is refused
("The model file is damaged.") and, when it was downloaded, deleted.

## Bundled (this folder)

| File | Scale | Size (bytes) | SHA-256 | Source |
|---|---|---|---|---|
| `FSRCNN_x2.pb` | 2 | 38973 | `366b33f0084c7b3f2bf6724f0a2c77bca94fcec9d7b6d72389d330073b380d5c` | https://github.com/Saafke/FSRCNN_Tensorflow/raw/master/models/FSRCNN_x2.pb |
| `FSRCNN_x4.pb` | 4 | 41661 | `5c68d18db561aed8ead4ffedf1b897ea615baaf60ebf6c35f8e641f8fa4a21bf` | https://github.com/Saafke/FSRCNN_Tensorflow/raw/master/models/FSRCNN_x4.pb |

FSRCNN: "Accelerating the Super-Resolution Convolutional Neural Network" (Dong, Loy, Tang, 2016); TensorFlow
port and trained weights by Xavier Weber (Saafke/FSRCNN_Tensorflow). Licence: Apache-2.0.

## Downloaded on first use (into `<data>\models`)

| File | Scale | Size (bytes) | SHA-256 | Source |
|---|---|---|---|---|
| `EDSR_x2.pb` | 2 | 38490162 | `585623221baa070279a0d1e7e113a4c3faba0f318ca7fdd9a65d9afc0763d9b4` | https://github.com/Saafke/EDSR_Tensorflow/raw/master/models/EDSR_x2.pb |
| `EDSR_x4.pb` | 4 | 38573255 | `dd35ce3cae53ecee2d16045e08a932c3e7242d641bb65cb971d123e06904347f` | https://github.com/Saafke/EDSR_Tensorflow/raw/master/models/EDSR_x4.pb |

EDSR: "Enhanced Deep Residual Networks for Single Image Super-Resolution" (Lim et al., 2017); TensorFlow port and
trained weights by Xavier Weber (Saafke/EDSR_Tensorflow). Licence: Apache-2.0.

Both repositories are the sources named by the OpenCV `dnn_superres` module documentation (opencv_contrib,
`modules/dnn_superres/README.md`). Hashes computed on 2026-10-21 from the files at the URLs above; each file was
loaded with OpenCV 5.0 `dnn_superres` and upscaled a test image by its scale.
