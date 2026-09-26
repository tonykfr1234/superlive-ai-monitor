# VMware Guide

## Required settings

1. Shut down the VM completely before changing virtual hardware.
2. Start with `1 processor × 6 cores`.
3. Allocate 8–16 GB RAM depending on host capacity.
4. Enable `Accelerate 3D graphics`.
5. Enable `Virtualize Intel VT-x/EPT` for nested virtualization.
6. Install VMware Tools.
7. Install MuMu and verify SuperLive can play the live view before starting AI.

## Nested virtualization

MuMu is itself an Android virtualization workload. Running it inside VMware requires nested virtualization. Host Hyper-V/VBS configurations can interfere with Workstation nested VT-x/EPT on some systems; validate MuMu before debugging the AI layer.

## GPU behavior

The guest normally receives VMware's virtual display adapter. Workstation 3D acceleration can use the host GPU for graphics rendering, but this does not expose an NVIDIA CUDA compute device to PyTorch. CPU fallback is therefore a normal VMware configuration.

## Remote desktop/session warning

MSS/BitBlt captures the interactive desktop. A remote-control disconnect may be harmless if the Windows user session stays logged in, but a real logoff destroys the GUI source. Keep the session logged in for this backend.
