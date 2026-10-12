<p align="center">
  <a href="README.md">English</a> · <a href="README.zh-CN.md">简体中文</a> · <a href="README.zh-TW.md">繁體中文</a> · <a href="README.ja.md">日本語</a> · <a href="README.ko.md">한국어</a><br>
  <a href="README.de.md">Deutsch</a> · <a href="README.es.md">Español</a> · <a href="README.fr.md">Français</a> · <strong>Português (Brasil)</strong> · <a href="README.vi.md">Tiếng Việt</a> · <a href="README.id.md">Bahasa Indonesia</a> · <a href="README.it.md">Italiano</a>
</p>
<h1 align="center">SuperLive AI Monitor</h1>
<p align="center">Selecione um polígono, grave a atividade de pessoas e receba os vídeos no Telegram.</p>
<p align="center">
  <img src="https://img.shields.io/badge/platform-Windows-0078D6" alt="Windows">
  <img src="https://img.shields.io/badge/Python-3.12-3776AB" alt="Python 3.12">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="Licença MIT"></a>
</p>
<p align="center"><a href="#quick-start">Instalação</a> · <a href="#workflow">Funcionamento</a> · <a href="#features">Recursos</a> · <a href="#demo">Demonstração</a> · <a href="#faq">Perguntas</a> · <a href="#documentation">Documentação</a></p>

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\installer\INSTALL_FROM_REPO.ps1
```

---

O SuperLive AI Monitor adiciona gravação por eventos à imagem ao vivo do SuperLive, exibida no MuMu no Windows. O YOLO detecta pessoas dentro de um polígono escolhido, e os vídeos concluídos são enviados pelo Telegram. Não é necessário conhecer a URL RTSP da câmera.

Este projeto comunitário não é afiliado aos fabricantes do SuperLive ou do MuMu. As 12 versões de idioma traduzem a documentação; a interface de visualização do aplicativo continua em chinês tradicional.

<a id="quick-start"></a>
## Instalação rápida

1. Instale o MuMu e o SuperLive, entre na sua conta e abra a câmera desejada. Mantenha a sessão do Windows conectada.
2. Clone ou baixe o repositório. Execute `installer\INSTALL_FROM_REPO.bat` ou o comando PowerShell acima.
3. Execute `installer\SET_TELEGRAM.bat` para configurar seu próprio bot e chat localmente. O repositório não inclui credenciais.
4. Com a câmera aberta, execute `installer\VERIFY_ALL.bat` para diagnósticos de captura, arquivos, compilação Python e codificador.
5. Inicie `%USERPROFILE%\Downloads\SuperLive_AI\Start_AI_Monitor.bat`. Para encerrar, use `Stop_AI_Monitor.bat` na mesma pasta.

**Já está instalado?** Execute `installer\UPDATE_FROM_REPO.bat`. A atualização faz backup, encerra o programa e preserva as configurações locais de região, Telegram e armazenamento. Reinicie manualmente após conferir a câmera. Não é criada inicialização automática, tarefa agendada ou serviço do Windows.

<a id="workflow"></a>
## Como funciona

```text
Nova atividade de uma pessoa dentro do polígono
  → iniciar gravação, alerta local do Windows e imagem de evidência
  → primeiros 120 segundos: sem troca automática de vídeo nem novos envios ao Telegram
  → movimento entre 120 e 300 segundos: encerrar o vídeo e iniciar outro imediatamente
  → cada novo vídeo inicia outro período de silêncio de 120 segundos
  → 120 segundos sem movimento ou limite de 300 segundos: encerrar e aguardar
  → vídeos concluídos aguardam na fila até terminar o período de silêncio atual
```

Uma pessoa vista pela primeira vez após a inicialização conta como nova atividade. Vídeos concluídos automaticamente duram normalmente **120–300 segundos**. Antes de 120 segundos, movimento reinicia o contador de inatividade sem dividir o vídeo. A partir de 120 segundos e antes do limite de 300, um movimento encerra o vídeo atual e inicia outro imediatamente. Ausência e imobilidade contam como falta de movimento; após 120 segundos contínuos sem movimento, a gravação para e aguarda novo movimento ou nova chegada.

**Somente vídeos concluídos geram notificações automáticas no Telegram.** O início mantém o alerta do Windows e a evidência local, sem enviar foto ao Telegram. Nenhum novo envio começa nos primeiros 120 segundos do vídeo atual, incluindo vídeos antigos e novas tentativas. Trocas sucessivas de vídeo podem prolongar a espera da fila. Uma requisição enviada antes desse período ainda pode chegar durante ele e não pode ser cancelada retroativamente.

Parada manual ou falha de captura pode deixar um vídeo parcial local com menos de 120 segundos. Não são adicionados quadros para inventar duração, e o envio automático aguarda o prazo de silêncio correspondente e qualquer período de silêncio de um vídeo mais recente.

Seleção de região, falha de captura ou resultado de detecção indisponível são estados desconhecidos: reiniciam o intervalo de observação, sem contar como inatividade confirmada.

<a id="features"></a>
## Recursos

| Recurso | Comportamento |
| --- | --- |
| Região | Polígonos convexos ou côncavos; compatibilidade com retângulos antigos |
| Detecção | Apenas pessoas; é necessária sobreposição com área positiva, não só contato na borda |
| Imagem | Exclui a barra de título/menu do MuMu; preserva horário e nome do canal da câmera |
| Gravação | Normalmente 120–300 segundos; movimento após 120 segundos inicia outro vídeo; parada após 120 segundos sem movimento |
| Entrega | Alerta e evidência locais no início; somente vídeos H.264 MP4 no Telegram, com fila e tentativas suspensas durante o período de silêncio |
| Armazenamento | 10 FPS; `C:\SuperLive_Recordings`; exclusão de vídeos antigos ao atingir 80% do disco, até 78% |
| Recuperação | Novas tentativas de captura e supervisores separados para monitor e gravador |

O movimento é estimado pela posição e pelo tamanho da caixa da pessoa, não por análise de postura. Pequenas oscilações são filtradas, e deslocamentos lentos se acumulam. Movimento da câmera, oclusão e erros de detecção podem afetar o resultado. O vídeo preserva toda a imagem recortada da câmera, sem limitar a gravação ao formato do polígono.

<a id="demo"></a>
## Demonstração

Demonstração silenciosa de **36 segundos**, com o renderizador real da interface, cenário sintético, caixas de pessoas definidas por roteiro e estados de gravação simulados. As legendas são bilíngues. Os intervalos de **120 segundos estão comprimidos** para mostrar o silêncio inicial no Telegram e a troca de vídeo por movimento; não é uma gravação real nem prova da precisão do YOLO.

![Demonstração gerada de seleção e gravação](docs/media/superlive-demo.gif)

[Abrir ou baixar a demonstração em MP4](docs/media/superlive-demo.mp4)

| Seleção do polígono | Gravação de atividade | Inatividade e nova ativação |
| --- | --- | --- |
| ![Selecionar vértices](docs/media/polygon-selection.png) | ![Gravar atividade na região](docs/media/activity-recording.png) | ![Aguardar novo movimento](docs/media/inactivity-rearm.png) |

<a id="controls"></a>
## Controles

| Controle | Ação |
| --- | --- |
| R / selecionar | Começar um polígono |
| Clique esquerdo | Adicionar pelo menos três vértices distintos na ordem do contorno |
| Clique direito / Backspace | Remover o último vértice |
| Enter / salvar | Fechar e salvar um polígono com área e sem bordas cruzadas |
| Esc / cancelar | Manter a região anterior |
| C / tela inteira | Remover a restrição de região |
| Q / parar | Encerrar o monitor e finalizar o vídeo atual |

Os atalhos exigem foco na janela de visualização. A região é salva em coordenadas relativas à imagem da câmera. Se não for possível converter uma região antiga com segurança, selecione outra ou escolha explicitamente a tela inteira.

<a id="faq"></a>
## Perguntas frequentes

**Por que a pessoa parada não reinicia a gravação?** Após uma parada por inatividade, é necessário novo movimento ou uma nova chegada. **Qual é a duração?** Normalmente 120–300 segundos; movimento após 120 segundos inicia outro vídeo. Interrupções podem gerar arquivos locais mais curtos, sem preenchimento artificial.

**Funciona no VMware?** Há suporte a inferência por CPU. O MuMu precisa de virtualização aninhada funcional; aceleração gráfica do VMware não fornece CUDA. **Por que um vídeo ainda não chegou?** O período de silêncio atual bloqueia novos envios e tentativas, inclusive de vídeos antigos; trocas sucessivas prolongam a espera. Falhas de rede também mantêm vídeos na fila. O PASS de configuração só confirma a existência do arquivo; configure suas credenciais com `SET_TELEGRAM.bat` e confira o recebimento real.

<a id="docs"></a>
<a id="documentation"></a>
## Documentação e segurança

[Guia em inglês](docs/USER_GUIDE.md) · [Guia em chinês tradicional](docs/USER_GUIDE.zh-TW.md) · [Arquitetura](docs/ARCHITECTURE.md) · [Gravação](docs/RECORDING.md) · [Hardware](docs/HARDWARE.md) · [VMware](docs/VMWARE.md) · [Alterações](CHANGELOG.md) · [Segurança](SECURITY.md)

O código fica em `src/`, os modelos de configuração em `config/` e os instaladores em `installer/`. Não publique credenciais, logs, imagens de eventos, gravações ou backups privados. Antes de enviar alterações, execute `python .\scripts\security_scan.py .`. Testes offline não substituem a validação com sua câmera e sua conta Telegram.

## Licença

[MIT](LICENSE).
