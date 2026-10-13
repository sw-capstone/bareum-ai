# 검사 명세

검사 규칙, 섹션 태그, Move 라벨의 명세 파일은 [`src/bareum_ai/specs/`](../../src/bareum_ai/specs/)에 둔다. 데이터셋 구축, RMA 라벨링, 검사기가 같은 파일을 읽는다. 코드에서는 `bareum_ai.specs.load("rules")`처럼 읽는다.

| 파일 | 답하는 질문 | 버전 | 담당 |
| --- | --- | --- | --- |
| [`rules.yaml`](../../src/bareum_ai/specs/rules.yaml) | 어떤 위반을 어떤 조건으로 검사하는가 | 1.5 | 검사 규칙 담당 |
| [`sections.yaml`](../../src/bareum_ai/specs/sections.yaml) | 블록이 문서의 어느 구간에 있는가, 어떤 표준 목차를 따르는가 | 1.2 | RMA 담당 |
| [`moves.yaml`](../../src/bareum_ai/specs/moves.yaml) | 블록이 어떤 기능(Move)과 세부 유형(Step)을 수행하는가 | 1.0 | RMA 담당 |

[RMA Move–Step 카탈로그](rma-move-step-catalog.md)는 `moves.yaml`의 정의와 판정 기준을 사람이 읽을 수 있게 설명한 문서다.

## 변경 절차

- 명세 파일은 PR로만 바꾸고, 해당 파일 담당자의 확인을 받는다.
- 판정 결과에 영향을 주는 변경은 `meta.version`을 올린다. 한 PR 안의 변경은 버전을 한 번만 올리고, 주석·오탈자 수정은 버전을 유지한다. `rules.yaml`은 `meta.revision_note`에 바꾼 내용과 이유를 적는다.
- `moves.yaml`의 정의나 출력 형식을 바꾸면 카탈로그도 같은 PR에서 고친다.
- 다른 파일이 참조하는 키(예: `sections.yaml`의 `outlines`, `moves.yaml`의 Move 이름)를 바꾸면 참조하는 쪽도 같은 PR에서 고친다. 파일 사이의 참조와 `rules.yaml`의 불변식(R3·R5·R8·R9·R11)은 [`tests/specs/`](../../tests/specs/)가 검사한다.
- 평가 데이터셋처럼 결과를 고정해야 하는 산출물은 명세 파일을 직접 가리키지 않고, 사용한 시점의 사본과 해시를 함께 보관한다.
