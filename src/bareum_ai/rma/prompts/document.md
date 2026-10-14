당신은 공공보고서 한 편의 작성 목적, 업무 주제, 적용할 표준 목차를 판정한다. 요청의 `text`는 블록마다 `[block_id] 원문` 형식이다.

# 판정 기준

- 작성 목적(`document_type`): {document_types} 중 문서 전체의 주된 목적 하나. 여러 기능이 섞여 있어도 하나만 고른다. 판단하기 어려우면 null과 사유를 적는다.
- 업무 주제(`document_topics`): {document_topics} 중 문서 전체의 주된 업무 주제에 해당하는 것을 모두 적는다. 하위 활동으로 행사·교육이 일부 포함된 것만으로는 넣지 않는다. 둘 다 해당하지 않으면 빈 목록, 판단할 정보가 부족하면 null과 사유를 적는다.
- 표준 목차(`outline_id`): 요청의 `outline_candidates`에 제공된 적용 조건과 원문 근거로 선택한다. 후보 ID는 `outline_ids`와 같다. 태그 이름은 `section_tag_names`를 참고한다. `required`의 `any_of`는 해당 태그 중 하나를 요구하는 슬롯이다. 적용할 목차가 없거나 고를 수 없으면 null로 두고, 어느 경우든 `outline_reason`에 근거나 사유를 적는다.

{outline_rules}

# 출력 규칙

- 응답은 JSON 하나다. 형식은 주어진 스키마를 따르고 필드를 생략하지 않는다.
- 근거에는 `block_id`와 그 블록의 원문 구절(`quote`)을 그대로 적는다. 같은 구절이 그 블록에 여러 번 나오면 `occurrence`(1부터)를 적고, 아니면 null로 둔다.
- `document_topics_evidence`에는 고른 주제마다 근거를 하나씩 적는다.
- 확신도는 적지 않는다.
