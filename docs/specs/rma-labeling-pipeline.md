# RMA 라벨링 파이프라인 설계

파서 출력에 Move·Step 라벨을 붙여 [`moves.yaml`](../../src/bareum_ai/specs/moves.yaml)의 `output` 형식으로 저장하는 과정을 정한다. 정의와 판정 기준은 [RMA Move–Step 카탈로그](rma-move-step-catalog.md)를 따른다. 라벨의 정확도를 재는 지표와 방식은 별도로 설계한다(12절).

수치는 Dev 60문서 실측(파서 1.7.5 출력)이다.

## 1. 흐름

```text
파서 출력 + 섹션 태그
  → 대상 선정 (3절)
  → 문서 요청 → 라벨러 → 문서 응답 검증 (5.1, 6절)
  → 블록 요청(구간별) → 라벨러 → 블록 응답 검증 (5.2, 6절)
  → 조립: 근거 위치 채우기, 미리 정한 표 상태 합치기 (7절)
  → 문서별 라벨 파일 (rma-annotations-2)
```

라벨러는 모델 또는 사람이다. 둘은 같은 요청을 받고 같은 형식으로 응답하며 같은 검증기를 통과해야 한다.

## 2. 모듈 구성

| 위치 | 역할 |
| --- | --- |
| `src/bareum_ai/sections/` | `sections.yaml`로 섹션 태그 부여 (붙임 구간 판별에 사용) |
| `src/bareum_ai/rma/targets.py` | 대상 선정, 텍스트 정규화, 표 행 구성, 묶음 나누기 |
| `src/bareum_ai/rma/request.py` | 문서·블록 요청 생성 |
| `src/bareum_ai/rma/validate.py` | 응답 검증 (6절) |
| `src/bareum_ai/rma/assemble.py` | 근거 위치 채우기, 문서별 라벨 파일 조립 |
| `src/bareum_ai/rma/prompt.py`, `src/bareum_ai/rma/prompts/` | `moves.yaml`에서 프롬프트와 응답 JSON 스키마 생성 (9절) |
| `src/bareum_ai/rma/labelers.py` | 라벨러 연결부. 고정 응답 라벨러(테스트·재현), OpenAI API 라벨러 |
| `src/bareum_ai/rma/runner.py` | 문서 하나의 요청·검증·재요청·조립 (8절) |
| `src/bareum_ai/dataset/rma/` | 코퍼스 일괄 실행과 실행 기록(8절) |
| `src/bareum_ai/dataset/rma/pilot.py` | 파일럿 문서·섹션 선정과 요청 고정 (11절) |
| `src/bareum_ai/dataset/rma/review.py`, `review.html` | 사람 라벨링·조정 화면과 확정 (10절) |
| `tests/rma/` | 위 모듈 테스트. 모델 호출 없이 고정 응답으로 검증한다 |

`bareum_ai.rma`는 서비스에서도 같은 순서로 쓸 수 있게 데이터셋 도구와 분리한다. 프롬프트 경로는 첫 구현 PR에서 [저장소 구조](../architecture/repository-structure.md)에 함께 반영한다.

## 3. 대상 선정

| 블록 | 처리 | Dev 수 |
| --- | --- | ---: |
| `item`, `para`, `note`, `attachment` | 판정 대상. 블록 하나에 annotation 1개 이상 | 4,653 |
| `heading` | 판정 대상. 구간 이름만 있으면 빈 annotations 허용 | 249 |
| `table` (본문) | 행 단위 판정 대상 | 167표 · 791행 |
| `table` (붙임, `SEC_ANNEX`) | `not_targeted`, 사유 `annex_table` | 69표 |
| `table` (`requires_vision` 또는 표 구조 경고) | `unreadable`, 사유 기록 | 본문 13표 |
| `document_title` | 문맥으로만 제공. 문서 단위 판정의 근거 | 58 |
| 원문이 빈 블록 (`붙임` 표시만 있는 붙임 머리 등) | 문맥으로만 제공, 사유 `empty_text` | 26 |
| `figure`, `approval`, `header`, `footer`, `page_number`, `title_fragment`, 파서가 `excluded_from_retrieval`로 표시한 블록 | 제외 (`rules.yaml` R7) | 538 |

`excluded_from_retrieval`이 붙은 블록도 제외한다. 결재 영역은 파서의 최종 제외 여부만 따르며 영역 경계로 본문을 다시 제외하지 않는다([파싱 결과 고정과 라벨 연결](../guide/parsing-snapshots.md)). 파서가 확정하지 못해 본문에 남긴 결재란 표(Dev 3개)는 라벨러가 `OTHER`로 판정한다. 표의 첫 행이 늘 열 제목인 것은 아니므로 열 제목 행은 라벨러가 판정한다(`moves.yaml` `labeling.table.header`).

## 4. 텍스트 정규화

요청에 담는 문자열은 파서의 `text`(표는 `rows[row][col]`)를 다음 순서로 정규화한다.

1. 유니코드 NFC
2. `\r\n`, `\r`을 `\n`으로 통일
3. `\n`, `\t`을 뺀 제어 문자 제거 (Dev 일부 표 셀에 원문에서 온 `\u0001`이 있다)

정규화는 문자를 지우거나 바꾸기만 하므로 파서 원본 위치로 되돌릴 수 있다. 근거 위치(`start`, `end`)는 정규화한 문자열 기준이다.

## 5. 요청과 응답

모든 요청은 `request_id`를 가진다. `request_id`는 `request_id`를 뺀 요청을 정렬된 JSON으로 직렬화한 SHA-256이다. 요청에는 `parsed_sha256`(파서 출력 파일 해시), `moves_sha256`, `sections_sha256`을 넣어 어떤 입력과 명세로 만든 요청인지 남긴다.

### 5.1 문서 요청 `rma-doc-request-1`

문서 단위 필드(`document_type`, `document_topics`, `outline_id`)를 먼저 판정한다. 블록 판정의 입력에 작성 목적이 필요하기 때문이다(카탈로그 4.1).

| 필드 | 내용 |
| --- | --- |
| `document` | `document_id`, 제목, 섹션 목록(`section_id`, `head_text`, `tag`) |
| `outline_ids` | `sections.yaml`의 `outlines` 키 목록 |
| `text` | 블록 순서대로 `[block_id] 텍스트`를 이어 붙인 문서 전문. 표는 행마다 `열 제목: 값` 형식 |

응답 `rma-doc-response-1`은 `moves.yaml` `output.document`와 같다. 근거 구절은 `quote`만 적는다.

### 5.2 블록 요청 `rma-block-request-1`

**묶음:** 섹션을 쪼개지 않는 단위로 쓰고, 이웃한 섹션은 판정 단위(블록 1개 또는 표 행 1개) 합이 40개 이하인 동안 통째로 이어 붙인다. 섹션 하나가 40개를 넘으면 그 섹션만 연속 블록으로 나눈다. 표는 나누지 않으며, 한 표가 40행을 넘으면 그 표만 단독 요청으로 보낸다. 섹션 밖 블록(`section_id`가 null)은 이어진 블록끼리 하나의 섹션처럼 다룬다. Dev 60문서 기준 요청은 213개(문서당 중앙값 2개), 요청당 판정 단위는 중앙값 30개다.

**문맥:**

- 문서 응답의 `document_type`, `document_topics`
- 문서 정규화 텍스트가 15,000자 이하이면 문서 전문 (Dev 90% 이상이 해당)
- 15,000자를 넘으면 묶음이 속한 섹션 전체, 대상 블록의 상위 블록 사슬, 묶음 앞뒤 3블록, 모든 섹션 제목

**표 직렬화:** 표는 `cells` 기준으로 행마다 셀 목록을 만든다. 병합으로 덮인 셀은 원문이 없으므로 `{"col": c, "text": "<시작 셀 값>", "inherited_from": [r, c]}`로 넣어 행을 이어 읽게 한다. 근거는 `inherited_from`이 가리키는 시작 셀로만 적을 수 있다.

**대상 목록:** `target_block_ids`(일반 블록)와 `target_table_ids`(본문 표). 표는 행 역할(`header`·`body`·`empty`)까지 라벨러가 판정한다.

### 5.3 블록 응답 `rma-block-response-1`

`moves.yaml` `output.blocks`와 같은 구조다. 근거(span)는 `quote`만 적고, 같은 구절이 원문에 여러 번 나오면 `occurrence`(1부터)를 함께 적는다. 표 근거에는 `row`, `col`을 함께 적는다. `start`, `end`는 조립 단계가 채운다.

```json
{
  "schema_version": "rma-block-response-1",
  "request_id": "…",
  "blocks": [
    {
      "block_id": "doc_…_b0012",
      "primary_annotation_id": "a2",
      "annotations": [
        {"annotation_id": "a1", "move": "GROUNDS",
         "evidence_span": {"quote": "관련 조례에 따라"},
         "context_block_ids": [],
         "steps": [{"step": "ORDINANCE", "evidence_span": {"quote": "관련 조례"}}],
         "step_status": "determined", "step_reason": null,
         "undetermined_reason": null, "spec_items": []},
        {"annotation_id": "a2", "move": "ACTION",
         "evidence_span": {"quote": "시스템을 도입할 계획"},
         "context_block_ids": [],
         "steps": [], "step_status": "not_applicable", "step_reason": null,
         "undetermined_reason": null, "spec_items": []}
      ],
      "table": null
    }
  ]
}
```

## 6. 응답 검증

검증은 형식과 원문 근거의 실재만 확인한다. 통과가 의미상 정답이라는 뜻은 아니다.

| 코드 | 검사 |
| --- | --- |
| V00 | 응답이 JSON이다 |
| V01 | `schema_version`과 `request_id`가 요청과 일치한다 |
| V02 | 요청한 블록·표가 정확히 한 번씩 있고, 요청하지 않은 식별값이 없다 |
| V03 | `annotation_id`가 형식(`a{n}`, 표는 `r{행}.a{n}`)에 맞고 블록 안에서 중복되지 않는다 |
| V04 | `primary_annotation_id`는 null이거나 같은 블록(표는 같은 행)의 annotation이다. 표 블록 수준은 null이다 |
| V05 | `move`가 13개 Move와 `OTHER`, `UNDETERMINED` 중 하나다 |
| V06 | Step 조합과 상태가 맞다 (아래 표) |
| V07 | `UNDETERMINED`이면 `undetermined_reason`이 세 사유 중 하나이고 steps·spec_items는 빈 목록이다. 그 밖의 Move는 `undetermined_reason`이 null이다 |
| V08 | `spec_items`는 SPEC에만 있고 SPEC은 1개 이상 가진다. `spec_field`는 허용 항목이거나 null이며 null이면 `reason`이 있다. `label_span`은 표에서만 쓴다 |
| V09 | 모든 span의 `quote`가 비어 있지 않고 대상 원문에서 찾아진다. 여러 번 나오면 `occurrence`가 있어야 한다 |
| V10 | 표 span은 같은 행, 열 제목 행, 그 행을 덮는 병합 시작 셀 중 하나를 가리킨다. 덮인 빈 셀은 가리키지 않는다 |
| V11 | 표는 모든 행을 한 번씩 가진다. `header_rows`·`row_header_cols`가 범위 안에 있고, `header`·`empty` 행은 annotation이 없으며 `body` 행은 1개 이상이다. `empty`는 모든 셀이 빈 행에만 쓴다. 표 블록의 `annotations`는 빈 목록이다 |
| V12 | annotation이 빈 목록인 일반 블록은 `heading`뿐이다 |
| V13 | `context_block_ids`는 같은 문서의 블록이다 |
| D01 | 문서 응답의 `document_type`은 다섯 값 중 하나이거나 null+사유, `document_topics`는 행사·교육의 부분 목록이거나 null+사유, `outline_id`는 `outlines` 키이거나 null+사유다. 근거 구절이 문서에 있다 |

**V06 Step 상태**

| `step_status` | 조건 |
| --- | --- |
| `not_applicable` | Step이 없는 Move. steps는 빈 목록, `step_reason`은 null |
| `determined` | Step이 있는 Move. steps 1개 이상, `step_reason`은 null |
| `partial` | Step이 있는 Move. steps 1개 이상, `step_reason` 있음 |
| `undetermined` | Step이 있는 Move. steps는 빈 목록, `step_reason` 있음 |

steps의 `step`은 해당 Move에 정의된 값이며 같은 Step을 두 번 적지 않는다.

## 7. 조립

1. span의 `quote`와 `occurrence`로 원문에서 `start`, `end`를 찾아 채운다.
2. 미리 정한 표 상태(`not_targeted`, `unreadable`)와 사유를 합친다.
3. 문서 응답과 블록 응답을 합쳐 문서 하나에 파일 하나(`rma-annotations-2`)를 만든다. 내용은 `moves.yaml` `output`이고 다음 기록을 덧붙인다.

| 필드 | 내용 |
| --- | --- |
| `run_id` | 실행 식별값 |
| `request_ids` | 이 문서에 쓴 요청 목록 |
| `labeler` | `{type: model, id: <모델 식별값>, prompt_sha256}` 또는 `{type: human, id: <라벨러 식별값>}` |
| `specs` | `moves_sha256`, `sections_sha256`, 파서 출력 스키마 버전 |
| `snapshot` | 고정 파싱 내용 해시, PDF SHA-256, 파서 구현 해시·설정·의존성 버전 |
| `block_sources` | 블록별 원문 전체·page·bbox·text 해시 (블록당 한 번) |
| `span_basis` | 블록/표 셀/문서 근거의 정규화 문자열 기준 |
| `failed_units` | 끝내 검증을 통과하지 못한 묶음과 오류 코드. 이 단위에는 라벨이 없다 |

라벨이 없는 단위에 의존하는 검사는 `not_executable`이다(`rules.yaml` R7).

## 8. 실행과 기록

- 검증에 실패한 응답은 오류 코드와 설명을 붙여 최대 2회 다시 요청한다. 그래도 실패하면 `failed_units`에 남긴다.
- 묶음 안 판정 단위의 30% 이상이 `CONTEXT_MISSING`이면 문서 전문을 문맥으로 넣어 1회 다시 요청한다.
- 실행마다 디렉터리를 하나 만들고 `manifest.json`에 Git SHA, 명세 해시, 파서 출력 스키마 버전, 라벨러 식별값, 프롬프트 해시, 생성 설정(온도 등), 실행 일시를 기록한다. 요청, 원 응답, 검증 결과, 조립 결과를 함께 둔다.
- 실행 결과물은 저장소에 넣지 않는다. 위치는 평가 자산 경로를 정할 때 함께 정한다.
- 라벨러 호출 자체가 실패하면(네트워크, 인증, 응답에 본문 없음) 검증 실패와 구분해 실행을 멈춘다.

**실행 방법**

```bash
export OPENAI_API_KEY=…   # 키는 환경 변수로만 넣는다. 저장소의 파일에 적지 않는다
uv run python -m bareum_ai.dataset.rma --split dev \
  --parsed-root <파서 출력 루트> --out <저장소 밖 실행 결과 루트> [--limit 2] [--reasoning-effort high]
```

실행 디렉터리에는 `manifest.json`과 문서마다 `documents/<document_id>/annotations.json`(라벨), `calls.jsonl`(호출마다 요청 id·시도·원 응답·검증 오류)이 생긴다.

Dev 60문서 1회 실행은 호출 273회(문서 60, 블록 213)이고, 지시문과 요청을 합친 입력이 약 510만 자다(재요청 제외). 비용은 키와 단가가 정해진 뒤 `--limit`로 시험 실행해 실제 토큰 수로 다시 계산한다.

## 9. 프롬프트

- 프롬프트는 `moves.yaml`에서 생성한다. Move의 `goal`·`boundary`·`example`, Step 정의, `labeling.rules`, `labeling.table`, 보조 라벨과 사유를 넣는다. 손으로 옮겨 적지 않으므로 `moves.yaml`이 바뀌면 프롬프트 해시도 바뀐다.
- 고정 문구(역할, 출력 규칙)는 `src/bareum_ai/rma/prompts/`의 템플릿에 둔다.
- 응답 형식은 구조화 출력(JSON 스키마, strict)으로 강제한다. 스키마는 `moves.yaml`의 Move·Step·SPEC 값으로 만들며, 6절 검증은 스키마와 관계없이 그대로 적용한다.
- 요청은 들여쓰기 없는 JSON으로 보낸다. 재요청에는 직전 응답과 검증 오류 목록을 덧붙인다.
- OpenAI API 라벨러는 키를 받기 전이라 실제 호출로 확인하지 않았다. 첫 시험 실행에서 요청·응답 형식과 구조화 출력 지원을 확인한다.
- 모델이 스스로 매긴 확신도는 받지 않는다(`moves.yaml` `labeling.evidence`).

## 10. 사람 라벨링 화면

라벨러용 사용법은 [RMA 파일럿 라벨링 안내](../guide/rma-pilot-labeling.md)에 있다.

파일럿과 검수에 쓰는 화면이다. `127.0.0.1`에서만 여는 로컬 서버와 HTML 한 장으로 되어 있고, 저장할 때마다 서버가 모델 응답과 같은 검증기로 검사해 결과를 화면에 돌려준다.

```bash
# 라벨링 (라벨러마다 따로 연다)
uv run python -m bareum_ai.dataset.rma.review serve --work <파일럿 작업 폴더> --annotator <이름>
# 조정 (두 라벨러의 판정을 나란히 보며 조정본을 만든다)
uv run python -m bareum_ai.dataset.rma.review serve --work <작업 폴더> --annotator adjudicated --reference <이름1> --reference <이름2>
# 확정 (검증을 통과한 문서를 rma-annotations-2로 조립)
uv run python -m bareum_ai.dataset.rma.review finalize --work <작업 폴더> --annotator <이름>
```

라벨은 `labels/<라벨러>/<document_id>.json`, 확정본은 `final/<라벨러>/<document_id>.json`에 저장된다.

- 판정 단위마다 카드를 보여 준다. 섹션 제목과 상위·인접 블록은 접어서 함께 보여 준다.
- 원문을 드래그하면 그 블록(표는 그 행)이 편집 대상이 되고 구절과 몇 번째 등장인지가 근거로 잡힌다. 표 셀은 더블클릭하면 셀 전체가 근거가 되며, 병합으로 덮인 셀은 시작 셀로 바뀐다.
- 근거를 고른 뒤 기능을 추가하고 Move를 고른다. Step 선택지는 고른 Move의 Step만 보여 준다. Step 상태와 사유, UNDETERMINED 사유를 고른다.
- 표는 행 단위 카드로 보여 주고, 셀을 눌러 근거를 지정한다. 열 제목 행과 행 제목 열을 표시할 수 있다.
- 결과는 `rma-block-response-1`, `rma-doc-response-1` 형식으로 저장되고 6절 검증기를 그대로 통과해야 확정할 수 있다. 바뀔 때마다 자동 저장한다.
- 라벨러 식별값을 기록하고 다른 라벨러의 결과는 보여 주지 않는다.
- 조정 모드는 기준 라벨러들의 판정을 단위마다 나란히 보여 주고, 한쪽 판정을 복사해 고칠 수 있다. Move·Step 조합이 다른 단위만 걸러 볼 수 있다. 이 비교는 조정을 돕기 위한 것이며 일치도 지표는 12절 평가 설계에서 정한다.

## 11. 파일럿

- Dev에서만 뽑는다. Test 문서는 쓰지 않는다.
- 선정과 요청 고정은 `uv run python -m bareum_ai.dataset.rma.pilot --parsed-root <파서 출력 루트> --work <작업 폴더>`로 한다. 요청은 선정 시점의 명세로 `requests/`에 고정해, 파일럿 도중 명세가 바뀌어도 저장한 라벨이 어긋나지 않게 한다.
- 문서 8편(계획 4, 결과 4)을 고르되 행사·교육 주제 문서를 2편 이상 넣는다. 주제는 라벨 전이므로 제목 키워드로 후보를 가린다. 문서마다 판정 단위 15~37개를 섹션 단위로 담는다(8편 × 37 ≈ 300). 고른 문서의 섹션 단위로 모든 판정 단위를 라벨링해 250~300개를 채운다. 문맥이 끊기지 않도록 단위를 흩어 뽑지 않는다.
- 표 본문 행 30개 이상, 구간 제목 20개 이상을 넣고 경계 혼동이 잦은 구간(추진 결과, 추진 계획, 기대 효과)을 포함한다.
- 2인이 독립 라벨링한 뒤 일치도를 재고 조정해 정답으로 만든다. 정답도 `rma-annotations-2` 형식이다.
- 파일럿 결과로 PROBLEM의 `CONSTRAINT` Step을 유지·병합·보류한다(카탈로그 2.5).

## 12. 평가와의 연결

평가 지표와 방식은 별도로 설계한다. 정답과 예측은 모두 `rma-annotations-2`이므로 평가 설계에서 다음을 정하면 바로 구현할 수 있다.

- 한 단위의 여러 annotation을 정답과 짝짓는 방법 (Move 일치만, 근거 구절 겹침까지)
- 주 기능(`primary_annotation_id`)을 평가에 넣는지
- 표 행을 일반 블록과 함께 셀지 따로 셀지
- Step, SPEC 항목, 문서 단위 필드의 평가 방법
- 라벨러 간 일치도 지표
- `failed_units`와 `not_targeted`·`unreadable`의 처리

## 13. 작업 순서

| 순서 | PR | 내용 | 예상 |
| --- | --- | --- | --- |
| R1 | 라벨링 핵심 모듈 | 2~7절. 고정 응답 기반 테스트 포함 | 3~4일 |
| R2 | 프롬프트와 실행기 | 8~9절. 모델 호출 어댑터와 실행 기록 | 2~3일 |
| R3 | 사람 라벨링 화면과 파일럿 표본 | 10~11절 | 2~3일 |
| R4 | 평가 스크립트 | 12절 설계 확정 후 | 1~2일 |

R1·R2가 끝나면 FMT·CON·EXP 평가 데이터셋의 EXP·CON 구간 라벨링에 쓸 수 있다. 이후 파일럿, 라벨러 모델 비교, 전체 라벨링 순으로 진행한다.

## 14. 미결 사항

- 라벨러 모델은 GPT-6 Sol로 정했다. API 키를 받은 뒤 시험 실행으로 호출 형식과 비용을 확인한다.
- `outline_id`의 행사교육 목차 적용 조건: 매뉴얼 확인 후 정한다(`sections.yaml` `outline_selection`).
- 실행 결과물 저장 위치: 평가 자산 경로와 함께 정한다.


## 파싱 입력 고정

파일럿 생성과 모델 실행 시 파싱 결과를 작업 폴더의 `snapshots/`에 복사해 내용 해시로 고정한다. 원래 캐시가 재생성되어도 작업은 고정 결과를 읽는다. 기존 `pilot.json`을 덮어쓰는 요청은 거절한다.

문서/블록 요청의 `parsed_sha256`은 실행 통계를 제외한 파싱 내용 해시다. 조립 시 다른 입력의 요청이 섞이면 중단하고, 모델 실행 결과를 가져올 때에도 현재 파일럿의 스냅샷과 원문 참조를 검증한다. 사람 라벨은 `rma-human-labels-2`로 저장하며 오래된 화면의 입력 ID가 현재 스냅샷과 다르면 저장을 거절한다.

문자 위치는 RMA 요청에 담긴 정규화 문자열 기준이다. 일반 블록은 `targets.normalize(block.text)`, 표는 `targets.table_rows()`의 셀 `text`, 문서 근거는 `request.block_text()` 기준이며 `source_text` 위치가 아니다. 상세 기준은 [파싱 결과 고정과 라벨 연결](../guide/parsing-snapshots.md)을 따른다.
