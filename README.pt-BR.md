<p align="center">
  <a href="README.md">English</a> · <a href="README.zh-CN.md">简体中文</a> · <a href="README.zh-TW.md">繁體中文</a> · <a href="README.ja.md">日本語</a> · <a href="README.ko.md">한국어</a><br>
  <a href="README.de.md">Deutsch</a> · <a href="README.es.md">Español</a> · <a href="README.fr.md">Français</a> · <strong>Português (Brasil)</strong> · <a href="README.vi.md">Tiếng Việt</a> · <a href="README.id.md">Bahasa Indonesia</a> · <a href="README.it.md">Italiano</a>
</p>
<p align="center"><img src="assets/superlive-ai.png" width="112" alt="SuperLive AI icon"></p>
<h1 align="center">Detecção de movimento de pessoas e veículos por captura de câmeras de vigilância</h1>
<p align="center">SuperLive AI Monitor · Compatível com os emuladores Android MuMu e MEmu</p>
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

Novo ícone e gravação de alta qualidade: resolução original da captura em H.264 CRF 18. O Telegram envia o mesmo vídeo original; apenas arquivos acima do limite precisam de uma cópia temporária comprimida, removida após o envio.

O SuperLive AI Monitor adiciona gravação por eventos à imagem ao vivo do SuperLive, exibida no MuMu no Windows. O YOLO detecta pessoas e veículos dentro de um polígono escolhido, e os vídeos concluídos são enviados pelo Telegram. Não é necessário conhecer a URL RTSP da câmera.

Este projeto comunitário não é afiliado aos fabricantes do SuperLive ou do MuMu. As 12 versões de idioma traduzem a documentação; a interface de visualização do aplicativo continua em chinês tradicional.

**Atualização de 21/10/2026:** O movimento de pessoas ou veículos (carros, motos, ônibus, caminhões) pode iniciar a gravação. Permanecem os 120 segundos de silêncio e vídeos de 120–300 segundos; o CSV diário é exceção. O Telegram distingue pessoas, tipos de veículo e placas realmente lidas, indicando as não confirmadas. O OCR analisa a imagem inteira e regiões sobrepostas, com recuperação automática após falhas. Roda do mouse, barra de rolagem e PageUp/PageDown permitem consultar até sete dias de histórico, incluindo análise de vídeos originais, ausência de leitura, candidatos não confirmados e erros.

Por padrão, ficam expandidos apenas os resultados confirmados que correspondem à lista privada com até duas edições de caracteres; os demais permanecem em uma seção recolhível. Clique no nome do vídeo para selecionar o original no Explorador de Arquivos do Windows.

O status do OCR, a qualidade e o progresso do lote permanecem junto aos controles inferiores. Com o reconhecimento de placas ativado, as correspondências ficam sempre visíveis à direita, agrupadas pela data de Taiwan com contagem diária e as mais recentes primeiro. Em janelas estreitas, a lista fica abaixo da câmera, que mantém a proporção da imagem. “其他紀錄” expande e leva aos outros registros; recolhê-los retorna às correspondências.


## Painel com quatro áreas

Vídeo ao vivo no canto superior esquerdo, histórico de placas à direita, última foto do veículo embaixo e controles agrupados. Arraste os títulos para trocar as áreas; L restaura a disposição. Exemplo sintético renderizado pela interface real.

![Dashboard diagram](docs/media/dashboard-guide.en.png)

[操作說明 / Guide](docs/DASHBOARD.en.md) · [排列動畫 / Layout animation](docs/media/dashboard-layout.gif)

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
Nova atividade de uma pessoa ou veículo dentro do polígono
  → iniciar gravação, alerta local do Windows e imagem de evidência
  → primeiros 120 segundos: sem troca automática de vídeo nem novos envios ao Telegram
  → movimento entre 120 e 300 segundos: encerrar o vídeo e iniciar outro imediatamente
  → cada novo vídeo inicia outro período de silêncio de 120 segundos
  → 120 segundos sem movimento ou limite de 300 segundos: encerrar e aguardar
  → vídeos concluídos aguardam na fila até terminar o período de silêncio atual
```

Uma pessoa ou veículo visto pela primeira vez após a inicialização conta como nova atividade. Vídeos concluídos automaticamente duram normalmente **120–300 segundos**. Antes de 120 segundos, movimento reinicia o contador de inatividade sem dividir o vídeo. A partir de 120 segundos e antes do limite de 300, um movimento encerra o vídeo atual e inicia outro imediatamente. Ausência e imobilidade contam como falta de movimento; após 120 segundos contínuos sem movimento, a gravação para e aguarda novo movimento ou nova chegada.

**Vídeos concluídos, placas confirmadas e relatórios CSV diários são enviados automaticamente pelo Telegram.** O início mantém o alerta do Windows e a evidência local, sem enviar foto ao Telegram. Nenhum novo envio começa nos primeiros 120 segundos do vídeo atual, incluindo vídeos antigos e novas tentativas. Trocas sucessivas de vídeo podem prolongar a espera da fila. Uma requisição enviada antes desse período ainda pode chegar durante ele e não pode ser cancelada retroativamente.

Parada manual ou falha de captura pode deixar um vídeo parcial local com menos de 120 segundos. Não são adicionados quadros para inventar duração, e o envio automático aguarda o prazo de silêncio correspondente e qualquer período de silêncio de um vídeo mais recente.

Seleção de região, falha de captura ou resultado de detecção indisponível são estados desconhecidos: reiniciam o intervalo de observação, sem contar como inatividade confirmada.

<a id="features"></a>
## Recursos

| Recurso | Comportamento |
| --- | --- |
| Região | Polígonos convexos ou côncavos; compatibilidade com retângulos antigos |
| Detecção | Pessoas e veículos; é necessária sobreposição com área positiva, não só contato na borda |
| Imagem | Exclui a barra de título/menu do MuMu; preserva horário e nome do canal da câmera |
| Gravação | Normalmente 120–300 segundos; movimento após 120 segundos inicia outro vídeo; parada após 120 segundos sem movimento |
| Entrega | Alerta e evidência locais no início; vídeos H.264 MP4, placas e relatórios CSV diários no Telegram, com fila e tentativas suspensas durante o período de silêncio |
| Armazenamento | 10 FPS; `C:\SuperLive_Recordings`; exclusão de vídeos antigos ao atingir 80% do disco, até 78% |
| Recuperação | Novas tentativas de captura e supervisores separados para monitor e gravador |

O movimento é estimado pela posição e pelo tamanho da caixa da pessoa ou do veículo, não por análise de postura. Pequenas oscilações são filtradas, e deslocamentos lentos se acumulam. Movimento da câmera, oclusão e erros de detecção podem afetar o resultado. O vídeo preserva toda a imagem recortada da câmera, sem limitar a gravação ao formato do polígono.

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

## Daily CSV · 23:00 Asia/Taipei

Às 23:00 (Taiwan), envia um CSV do dia entre 00:00 inclusive e 23:00 exclusive e salva junto aos vídeos. O CSV diário é exceção ao silêncio da gravação. Eventos posteriores são excluídos, sem complemento à meia-noite. Mesmo sem eventos há relatório; falhas e horários perdidos são recuperados.
