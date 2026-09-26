# Hardware Guide / 硬體建議

## Physical Windows host

Recommended 24/7 starting point:

- CPU: modern 6-core-or-better CPU
- RAM: 16 GB minimum, 32 GB preferred when MuMu and other workloads coexist
- GPU: NVIDIA CUDA GPU is useful for higher YOLO inference FPS; CPU fallback is supported
- Storage: SSD/NVMe; dedicated high-endurance recording storage is preferable
- Cooling: sustained CPU/GPU workloads need stable thermals

## VMware guest

Recommended starting point:

- 6 vCPU (`1 processor × 6 cores`)
- 8 GB RAM minimum; 16 GB if the host has enough memory
- `Accelerate 3D graphics` enabled
- about 2 GB graphics-memory allowance is normally enough for the GUI workload
- `Virtualize Intel VT-x/EPT` enabled because MuMu itself needs virtualization
- install VMware Tools

Do not over-allocate vCPU/RAM so aggressively that the host starts swapping.

## GPU clarification

VMware Workstation 3D acceleration helps the virtual desktop and MuMu rendering. It is **not equivalent to CUDA passthrough**. Seeing `VMware SVGA 3D` in the guest is expected. If `torch.cuda.is_available()` is false, the monitor uses CPU inference.

Intel Iris Xe is useful to the host graphics stack, but it is not an NVIDIA CUDA device. Future optimization for Intel-only systems may use OpenVINO rather than pretending VMware SVGA is a CUDA GPU.
