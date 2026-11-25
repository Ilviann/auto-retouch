# Candidate models for portrait retouching

Research recorded on 2026-11-19 for Auto ReTouch. The proposed first comparisons
are **AdaInt and CSRNet** for whole-photo color, **IAT Exposure** for lighting
errors, and **MediaPipe Selfie Multiclass** for selecting exposed skin. CSRNet,
IAT Exposure and Selfie Multiclass have since run locally. Portrait-quality
acceptance remains open. The IAT evaluation includes a comparison with Auto
exposure and tone on three local frames. [Current stage catalog](stages.md) ·
[IAT results](decisions/0007-iat-exposure.md)

## Whole-photo color, exposure and white balance

| Candidate | Checkpoint and intended use | What matters for Auto ReTouch |
| --- | --- | --- |
| **AdaInt** | ModelScope publishes `pytorch_model.pt`. The model predicts an image-specific 3D color lookup table for global grading. | Compare color consistency on skin, clothes and backdrop across differently framed shots from one studio setup. The authors also publish a distinct portrait-trained variant, `AiLUT-PPR10KA-sRGB.pth`. PPR10K limits its distributed dataset files to noncommercial research. [ModelScope](https://www.modelscope.ai/models/iic/cv_adaint_image-color-enhance-models) · [Author checkpoints](https://github.com/JavaZeroo/AdaInt) · [PPR10K terms](https://github.com/csjliang/PPR10K) |
| **CSRNet** | ModelScope publishes `pytorch_model.pt` for lightweight global photo retouching. | Compare with AdaInt for the same proposed **Auto color grade** stage. Include correctly exposed frames that should need very little adjustment. [ModelScope](https://www.modelscope.ai/models/iic/cv_csrnet_image-color-enhance-models) · [Paper](https://www.ecva.net/papers/eccv_2020/papers_ECCV/html/2014_ECCV_2020_paper.php) |
| **DeepLPF** | ModelScope publishes `pytorch_model.pt`. It learns graduated, elliptical and polynomial filters for local enhancement. | Candidate for uneven scene lighting, such as a dark subject against a bright backdrop. Check that it preserves intentional side lighting. [ModelScope](https://www.modelscope.ai/models/iic/cv_deeplpfnet_image-color-enhance-models) · [Paper](https://openaccess.thecvf.com/content_CVPR_2020/papers/Moran_DeepLPF_Deep_Local_Parametric_Filters_for_Image_Enhancement_CVPR_2020_paper.pdf) |
| **IAT Exposure** | The community ONNX conversion of the authors’ roughly 90,000-parameter network needs **both** `onnx/iat_exposure.onnx` and `onnx/iat_exposure.onnx.data`. This variant targets underexposure and overexposure. | Compare against the existing **Auto exposure and tone** stage. The conversion lists Apache 2.0 and identifies the upstream weights. Verify conversion fidelity, DirectML operator support and peak memory on full-resolution images. [ONNX model card](https://huggingface.co/eleasm/IAT-ONNX) · [File listing](https://huggingface.co/eleasm/IAT-ONNX/tree/main/onnx) · [Original project](https://github.com/cuiziteng/Illumination-Adaptive-Transformer) |
| **Retinexformer** | The author publishes `LOL_v2_real.pth` for real low-light images and separate weights for other datasets. | A rescue option for genuinely dark frames. Review noise, skin color and texture in lifted shadows. Commercial terms for the selected weights still need confirmation. [Author’s model card and weights](https://huggingface.co/CaiYuanhao/Retinexformer) |
| **Deep White-Balance Editing** | Corrects white balance in already-rendered sRGB images, including JPEGs. | A research comparison for a global color cast. The authors explicitly restrict the release to research use and exclude commercial use. [Author’s repository and terms](https://github.com/mahmoudnafifi/Deep_White_Balance) |
| **DCLAN** | The authors list `models/best.pth` for white balance correction in rendered photographs. | The documented implementation requires CUDA 11.8 for Mamba-SSM. A compatible development runtime and commercial terms for the weights remain open. [Author’s repository](https://github.com/YangCheng58/WB_Color_Space) |

The three ModelScope color models above share a checkpoint filename. The
repository ID identifies which weights to evaluate. Each publisher card lists
Apache 2.0 and identifies Adobe FiveK as training data. FiveK publishes separate
research-use terms for its photographs. Record the exact checkpoint revision,
its commercial-use evidence, and its redistribution terms before adoption.
[FiveK dataset terms](https://data.csail.mit.edu/graphics/fivek/) · [Project selection criteria](architecture.md#engine-selection-gate)

## Exposed skin and local skin-color correction

**MediaPipe Selfie Multiclass** labels face skin and body skin separately. A
community conversion publishes `onnx/model.onnx`, the source TFLite version,
a checksum and an Apache 2.0 license statement. The proposed **Exposed skin
mask** stage could supply these regions to **Even skin color**. That second
stage could reduce local redness or a cool cast using each person’s own skin
as a reference while preserving luminance. The color adjustment is a proposed
application algorithm. The network supplies the mask. Test full-length
portraits, small hands, and ownership of skin regions where people overlap.
[Google model card](https://storage.googleapis.com/mediapipe-assets/Model%20Card%20Multiclass%20Segmentation.pdf) · [ONNX conversion](https://huggingface.co/senty-au/selfie_multiclass_256x256-ONNX)

**Retouch4me Skin Tone** offers a commercial visual reference for redness and
cool or magenta casts across face, neck, arms and body. The publisher offers a
finished plugin. The search found no public checkpoint for integration.
[Publisher examples](https://retouch4.me/products/retouch-plugins/109?lng=en)

## Corrective dodge and burn

| Candidate | Relevance and availability |
| --- | --- |
| **happyinhappy/retouch-dodge-burn-onnx** | Predicts a neutral-gray Soft Light layer for local corrections. The publisher describes on-device CPU inference and explicitly withholds the weights. The examples are useful for judging a future adjustment-map preview. [Model card](https://huggingface.co/happyinhappy/retouch-dodge-burn-onnx) |
| **hewei3/qwen-image-dodge_burn** | A Qwen-Image-Layered fine-tune with public BF16 weights and an Apache 2.0 model card. The repository lists 57.7 GB and the publisher specifies at least one A100 for inference. The documented configuration exceeds the app’s 6 GB RTX minimum. [Model card](https://huggingface.co/hewei3/qwen-image-dodge_burn) · [Files](https://huggingface.co/hewei3/qwen-image-dodge_burn/tree/main) |
| **Aftershoot additive blend-map U-Net** | The AAAI 2026 paper describes a 6 MB model that predicts local brightness changes for facial retouching. Public weights and commercial checkpoint terms were unverified in this search. [Paper](https://ojs.aaai.org/index.php/AAAI/article/view/41481) |
| **Retouch4me Dodge&Burn** | A local Windows plugin with an editable Soft Light layer. Its before-and-after examples provide a visual reference for skin texture, hands and group shots. Integration rights and public weights were unverified. [Publisher examples](https://retouch4.me/dodgeburn) |

For visual review, compare skin at full resolution and facial shape at normal
viewing size. For batch review, compare adjacent images from the same lighting
setup. The local study portraits can support initial comparisons. Representative
multi-person coverage, full-resolution memory use, and minimum-GPU performance
remain open for these candidates. [Evaluation workflow](workflow.md#validation-on-the-available-machine)
