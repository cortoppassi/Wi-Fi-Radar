# Wi-Fi Radar

Aplicação local para visualizar os dispositivos conectados no seu Wi-Fi e fazer gerenciamento básico por interface web.

## O que já faz

- Varrredura da rede local com `ping` + `arp`.
- Lista de dispositivos com IP, MAC, hostname e status online/offline.
- Gerenciamento por dispositivo:
  - apelido (`nickname`)
  - marcação confiável
  - marcação de bloqueado
  - observações
- Atalho para abrir painel do roteador.
- Instruções guiadas para aplicar bloqueio por MAC no roteador.

## Requisitos

- Python 3.11+
- Windows, Linux ou macOS

## Como rodar

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Abra em `http://localhost:5050`.

## Observações

- O bloqueio marcado no app é um controle de gestão local. Para cortar internet de fato, aplique o bloqueio MAC no painel do roteador.
- Se quiser apontar para outro roteador, defina a variável:

```bash
set ROUTER_URL=http://192.168.1.254
```
# Wi-Fi-Radar
# Wi-Fi-Radar
