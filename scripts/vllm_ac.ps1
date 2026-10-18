# Qwen3.8-27B-NVFP4 vLLM sunucusunu WSL'de başlatır ve hazır olana kadar bekler.
# Kullanım:  powershell -ExecutionPolicy Bypass -File scripts\vllm_ac.ps1
# Adres:     http://127.0.0.1:8001/v1   (model adı: qwen3.8-27b-nvfp4)
# Not: GPU'da ~29 GB VRAM kullanır; RL eğitimleri çalışıyorsa başlatmaz.
wsl -d Ubuntu-24.04 -- bash -lc '~/qwen38_up.sh'
exit $LASTEXITCODE
