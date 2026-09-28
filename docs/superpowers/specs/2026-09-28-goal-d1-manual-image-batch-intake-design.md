# Goal D1 — Manual Image Batch Intake Design

## Status e boundary

Esta especificação define o futuro `Goal D1 — Manual Image Batch Intake`. Ela não implementa o
Goal. D1 transforma imagens criadas manualmente em `ImageCandidate`s associados às variantes de
uma campanha e prontos para o próximo estágio HeyGen.

D1 inclui:

- pasta de entrada preparada pela aplicação ou escolhida pelo usuário;
- manifest explícito `variantId → relative image path`;
- validação completa e dry-run antes de qualquer mutação;
- cópia não destrutiva para o work root;
- persistência, provenance, idempotência e aprovação opcional;
- CLI JSON e JSON Schema.

D1 não inclui geração de imagens, Google Flow, watcher de diretório, HeyGen, FastAPI, React, crop,
conversão, edição visual ou heurística por nome/ordem de arquivo. O processamento começa somente
quando o usuário executa o comando; a UI futura chamará o mesmo serviço por um botão explícito.

## Experiência operacional

O caminho recomendado é preparar uma pasta por campanha:

```text
imports/
  soulmate-sprint-01/
    image-import.json
    images/
      laundromat.png
      hotel.png
      bedroom.webp
```

O helper abaixo cria o diretório, a subpasta `images/` e um manifest preenchido com todas as
variantes atuais da campanha:

```powershell
auraly image prepare-import `
  --campaign soulmate-sprint-01 `
  --output imports/soulmate-sprint-01
```

O helper não cria imagens nem inventa filenames. Cada item começa com `path` vazio, para o usuário
preencher depois de colocar os arquivos na pasta.

Antes da importação, o usuário valida o lote:

```powershell
auraly image import-batch `
  --input imports/soulmate-sprint-01/image-import.json `
  --dry-run
```

Sem `--dry-run`, o mesmo plano validado é executado:

```powershell
auraly image import-batch `
  --input imports/soulmate-sprint-01/image-import.json
```

Não existe monitoramento automático da pasta. Isso evita importar um arquivo ainda sendo salvo e
mantém aprovação e custo futuro sob ação explícita do usuário.

## Contrato do manifest

`ImageImportBatch` é um contrato Pydantic versionado e fechado a campos desconhecidos:

```json
{
  "schemaVersion": "1.0",
  "campaignId": "soulmate-sprint-01",
  "approveImported": true,
  "approvedBy": "rafael",
  "items": [
    {
      "variantId": "laundromat",
      "path": "images/laundromat.png"
    },
    {
      "variantId": "hotel",
      "path": "images/hotel.png"
    },
    {
      "variantId": "bedroom",
      "path": "images/bedroom.webp"
    }
  ]
}
```

Regras do contrato:

- `schemaVersion` aceita somente `1.0` em D1;
- `campaignId` identifica uma campanha persistida;
- `variantId` é o identificador legível e único dentro da campanha, não o UUID interno;
- `path` é relativo ao diretório do manifest, nunca absoluto;
- `items` contém exatamente uma entrada para cada variante atual da campanha;
- `approveImported=true` exige `approvedBy` válido;
- `approveImported=false` exige `approvedBy=null` e cria candidatos em `pending_review`;
- variantes repetidas, desconhecidas, ausentes ou pertencentes a outra campanha invalidam o lote;
- dois itens não podem apontar para o mesmo arquivo físico.

O hash do manifest é calculado a partir da representação canônica validada, com chaves ordenadas,
UTF-8 e JSON compacto. Caminhos são normalizados para `/` antes do hash.

## Preparação do import

`prepare-import` é apenas um facilitador. Ele:

1. carrega a campanha e suas variantes em ordem determinística;
2. recusa sobrescrever um manifest existente;
3. cria a pasta `images/`;
4. grava atomicamente um `image-import.json` com `approveImported=false`, `approvedBy=null` e um item
   por variante;
5. devolve JSON com os paths criados.

O usuário também pode criar o manifest manualmente em qualquer diretório local. O diretório pai do
manifest passa a ser o source root confiável daquela operação.

## Planejamento e dry-run

O serviço expõe uma única operação de planejamento usada tanto por `--dry-run` quanto pela execução
real. O plano é imutável e contém, por item:

- `variantId` e `sceneVariantId` resolvido;
- source path relativo;
- formato real;
- largura, altura e orientação;
- byte count e SHA-256;
- destination path;
- ação prevista: `create`, `reuse` ou `conflict`;
- review status resultante.

O planejamento executa, nesta ordem:

1. parse estrito do JSON e do schema versionado;
2. resolução da campanha e snapshot das variantes;
3. verificação de cobertura e duplicidade;
4. contenção de cada source abaixo do diretório do manifest;
5. rejeição de traversal, symlink ou junction que escape o source root;
6. inspeção do conteúdo real com Pillow;
7. validação de PNG, JPEG ou WebP e correspondência entre conteúdo e extensão;
8. validação de orientação vertical (`height > width`);
9. cálculo streaming do SHA-256;
10. consulta de candidatos existentes e construção do plano.

D1 registra as dimensões, mas não inventa um limite mínimo de resolução. A validação de requisitos
específicos do provider pertence ao preflight HeyGen de D2A. Arquivo quadrado ou horizontal falha;
nenhum crop automático é realizado.

Qualquer erro deixa o plano inválido. Um plano inválido nunca copia arquivo nem abre uma transação
de escrita no banco.

## Publicação de arquivos

O destination path é content-addressed:

```text
work/campaigns/<campaign-id>/variants/<scene-variant-id>/images/imported/<sha256>.<ext>
```

O serviço:

1. abre novamente o source e confirma identidade, tamanho e hash observados no plano;
2. copia para arquivo temporário no diretório de destino;
3. sincroniza e reinspeciona o temporário;
4. publica sem sobrescrever o path final;
5. se o final já existe, aceita somente bytes com o mesmo hash e formato;
6. nunca move, altera ou remove o source do usuário.

Os helpers existentes de containment, inspeção e publicação exclusiva devem ser extraídos ou
reutilizados; D1 não mantém uma segunda implementação menos segura. Os nomes públicos podem deixar
de ser específicos de Flow, preservando wrappers compatíveis para os callers atuais.

## Modelo de domínio

### Decisão

`ImageCandidate` passa a representar um asset de imagem adquirido por geração ou import manual. Não
será criada uma entidade paralela `ImportedImage`, e não será criada uma `ImageGeneration` falsa.

Campos adicionados a `ImageCandidate`:

| Campo | Regra |
| --- | --- |
| `scene_variant_id` | FK obrigatória para a variante dona do asset |
| `source_kind` | `generated` ou `manual_import` |
| `image_generation_id` | obrigatório para `generated`; nulo para `manual_import` |
| `import_manifest_sha256` | obrigatório somente para `manual_import` |
| `import_source_path` | path relativo ao manifest; obrigatório somente para `manual_import` |

Os campos já existentes continuam sendo usados para artifact facts e review:

- `source_path`, `sha256`, `width`, `height`, `size_bytes`, `format`;
- `review_status` e todo o audit trail de aprovação, rejeição e supersession.

A migration:

1. adiciona e preenche `scene_variant_id` nos candidatos existentes pelo `ImageGeneration` pai;
2. marca candidatos existentes como `generated`;
3. torna `image_generation_id` opcional;
4. adiciona checks que exigem exatamente uma provenance válida;
5. adiciona índice único parcial para imports por `scene_variant_id + sha256`.

Consultas de aprovação deixam de depender de join com `ImageGeneration` para descobrir a variante.
Fluxos Google Flow continuam retornando o mesmo contrato, agora com provenance explícita.

### Por que não persistir `ImageImportBatch`

No MVP, o batch é um request/result síncrono, não uma operação longa. O manifest hash persistido em
cada candidato permite agrupar e auditar imports depois do restart. Erros de validação são resposta
do comando e, por definição, não criam estado.

Uma tabela própria só entra se surgir necessidade real de histórico de tentativas inválidas,
execução assíncrona ou retomada item a item. D1 não cria essa infraestrutura antecipadamente.

## Idempotência, aprovação e conflito

A identidade de um import é:

```text
campaign_id + scene_variant_id + content_sha256
```

Comportamento:

- candidato manual existente com a mesma variante e hash: `reuse`;
- arquivo final existente com o mesmo hash: reutilizado;
- rerun integral do mesmo manifest: nenhuma nova linha e nenhum novo arquivo;
- variante já com candidato aprovado do mesmo hash: reutiliza a aprovação;
- variante já com candidato aprovado de hash diferente: o batch inteiro falha no planejamento;
- `approveImported=true`: todos os novos candidatos entram aprovados na mesma transação;
- `approveImported=false`: todos entram em `pending_review`.

Substituição nunca é implícita. Para trocar uma imagem aprovada, o usuário importa a nova imagem
como pendente e usa o mecanismo existente `image candidate replace`. Isso preserva histórico e
impede troca acidental em lote.

## Consistência entre filesystem e SQLite

Filesystem e SQLite não compartilham uma transação. D1 usa o menor protocolo recuperável:

1. validar tudo sem mutação;
2. publicar todos os arquivos content-addressed;
3. executar uma única transação `BEGIN IMMEDIATE` para criar/reutilizar candidatos e aplicar review;
4. em falha normal do banco, remover apenas arquivos criados por esta execução que continuam
   byte-identical e sem referência;
5. após queda abrupta, aceitar arquivos finais sem linha como residue seguro e reutilizá-los no
   rerun.

Um arquivo órfão content-addressed não produz associação errada nem sobrescrita. O banco só fica
visível com o lote completo; não existe commit parcial de candidatos.

## Serviços e CLI

O módulo de imagens recebe:

- contratos `ImageImportBatch`, `ImageImportItem`, `ImageImportPlan` e `ImageImportResult`;
- `ImageImportService.prepare_directory()`;
- `ImageImportService.plan()`;
- `ImageImportService.execute(plan)`;
- métodos mínimos no repository para snapshot de variantes, lookup idempotente e persistência em
  lote.

Comandos:

```text
image prepare-import
image import-batch [--dry-run]
export-image-import-schema
```

Todos escrevem um único documento JSON em stdout. Erros usam códigos estáveis e mensagens
sanitizadas, sem conteúdo bruto da imagem ou paths absolutos desnecessários.

Exemplo de resultado:

```json
{
  "schemaVersion": "1.0",
  "status": "completed",
  "campaignId": "soulmate-sprint-01",
  "manifestSha256": "<sha256>",
  "total": 3,
  "created": 3,
  "reused": 0,
  "approved": 3,
  "items": []
}
```

O dry-run retorna o mesmo shape com `status=valid` e as ações previstas, sem IDs de entidades ainda
não persistidas.

## Erros esperados

O boundary mapeia para códigos estáveis, incluindo:

- `image_import_manifest_invalid`;
- `image_import_campaign_not_found`;
- `image_import_variant_coverage_invalid`;
- `image_import_source_path_invalid`;
- `image_import_media_invalid`;
- `image_import_orientation_invalid`;
- `image_import_source_changed`;
- `image_import_approved_candidate_conflict`;
- `image_import_artifact_conflict`;
- `image_import_persistence_failed`.

Falhas de validação reportam todos os problemas determinísticos encontrados no lote, em ordem de
`variantId`, para evitar ciclos de correção um erro por vez.

## Verificação

`tests/test_image_import_batch.py` cobre pelo menos:

- preparação de diretório e recusa de overwrite;
- batch válido com três variantes;
- dry-run sem mudança de filesystem ou banco;
- cobertura incompleta, variante duplicada e variante desconhecida;
- source duplicado entre itens;
- path absoluto, traversal, symlink e junction escapando o root;
- extensão falsa, arquivo corrompido e formato não suportado;
- imagem quadrada ou horizontal;
- source alterado entre plan e execute;
- aprovação explícita e import pendente;
- rerun idêntico sem duplicar arquivo ou candidato;
- conflito com aprovado de hash diferente antes da cópia;
- source externo intacto;
- restart preservando associação e provenance;
- rollback de banco sem candidatos parciais;
- migration dos candidatos gerados existentes.

Os testes de CLI cobrem JSON válido, dry-run, execução e exportação do schema. A entrega exige:

```powershell
uv run python scripts/verify.py fast --pytest tests/test_image_import_batch.py
uv run python scripts/verify.py full
```

Ambos devem passar em Windows e Linux.

## Critérios de saída

D1 está concluído quando:

- o usuário consegue preparar uma pasta, adicionar as imagens e importar mediante ação explícita;
- três imagens válidas deixam três variantes associadas e aprovadas para D2A;
- batch incompleto ou ambíguo falha antes de copiar ou persistir;
- rerun idêntico não duplica candidatos nem arquivos;
- sources externos permanecem intactos;
- restart preserva associação, review e provenance;
- candidatos Google Flow existentes continuam funcionando;
- schema, CLI e documentação operacional refletem o comportamento entregue.
