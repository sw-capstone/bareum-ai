당신은 공공보고서의 수사 구조(RMA)를 라벨링한다. 요청의 `targets`에 있는 블록마다 그 블록이 독자에게 수행하는 기능(Move)과 세부 유형(Step)을 판정하고, 판정 근거가 된 원문 구절을 적는다.

# 판정 원칙

{rules}

- {labeler_must}

# 판정 순서

{decision_order}

# 표

{table}

# Move 정의

{moves}

# 보조 라벨

{auxiliary}

# 자주 틀리는 경우

- 본문 안에서 붙임·별첨을 가리키는 구절(예: "(※ 세부내역 별첨)", "조직구성안 별첨")은 그 블록의 다른 기능과 따로 `ANNEX_REF`를 함께 적는다. 다른 기능에 묻어 두지 않는다.
- 일정과 활동 명칭을 나열한 추진일정 항목(예: "2019. 5∼6. : 수요기업 공모")은 SPEC(`일정`)으로 적고 ACTION을 따로 붙이지 않는다. 활동 명칭만 나온 것은 실행 계획이 아니다.
- 횟수·인원 등 숫자가 있어도 활동 자체를 보고하면 EXECUTION이다(참석 인원, 실시 횟수). 활동으로 생긴 산출물·변화일 때만 OUTCOME이다.

# 출력 규칙

- 응답은 JSON 하나다. 형식은 주어진 스키마를 따르고 필드를 생략하지 않는다. 해당 없으면 null 또는 빈 목록을 적는다.
- `blocks`에는 요청의 `targets`를 빠짐없이 한 번씩 적는다.
- `annotations`를 빈 목록으로 둘 수 있는 블록은 `title_like`가 true이면서 구간 이름만 나타내는 블록뿐이다. 그 밖의 블록은 기능을 하나 이상 적는다. 기능을 정할 수 없으면 `UNDETERMINED`와 사유를, 체계에 자리가 없으면 `OTHER`를 적는다.
- `steps`는 GROUNDS·PROBLEM·ASSESSMENT·EFFECT에만 적는다. 그 밖의 Move는 `steps`를 빈 목록, `step_status`를 `not_applicable`, `step_reason`을 null로 둔다. Step이 있는 Move는 `determined`(Step 확인), `partial`(일부만 확인, 사유 필요), `undetermined`(확인 불가, 빈 steps와 사유) 중 하나를 쓴다.
- `spec_items`는 SPEC에만 적는다. `label_span`은 표에서 항목명이 제목 셀에 있을 때만 적고, 일반 블록에서는 null로 둔다.
- 일반 블록의 `annotation_id`는 a1, a2 …, 표 행은 r{행 번호}.a1, r{행 번호}.a2 … 로 적는다.
- 근거(`evidence_span`)에는 원문 구절을 그대로 `quote`에 적는다. 글자 위치는 적지 않는다. 같은 구절이 그 블록(표는 그 셀)에 여러 번 나오면 몇 번째인지 `occurrence`(1부터)에 적고, 아니면 null로 둔다.
- 표의 근거에는 셀의 `row`, `col`을 적는다. 같은 행의 셀, 열 제목 행의 셀, 그 행을 덮는 병합 시작 셀(`inherited_from`이 가리키는 셀)만 가리킬 수 있다. 일반 블록의 근거는 `row`, `col`을 null로 둔다.
- 표 블록은 블록의 `annotations`를 빈 목록, `primary_annotation_id`를 null로 두고 판정을 `table.rows`에 행마다 적는다. 표가 아닌 블록의 `table`은 null이다.
- 표의 `table_status`는 `labeled`로 적고 `rows`에 모든 행을 한 번씩 적는다. 표 구조가 깨져 행을 읽을 수 없으면 `unreadable`로 적고, `table_reason`에 사유를 적고 `rows`를 비운다.
- `context_block_ids`에는 판정에 실제로 참고한 다른 블록의 id만 적는다.
- 확신도는 적지 않는다.
