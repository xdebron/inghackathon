# Qwen3.8 vLLM sunucusunu WSL'de durdurur ve GPU belleğinin boşaldığını gösterir.
# Kullanım:  powershell -ExecutionPolicy Bypass -File scripts\vllm_kapat.ps1
wsl -d Ubuntu-24.04 -- bash -lc '~/qwen38_down.sh'
exit $LASTEXITCODE
