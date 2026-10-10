# bareum-ai — AI 처리 저장소

문서 처리와 AI 기능, 관련 테스트와 평가를 관리하는 저장소다. 공개 API는 `bareum-server`, 화면은 `bareum-web`에서 관리한다.

## 현재 구성

AI 실행 코드 경로와 Python 개발 환경, 저장소 운영에 필요한 문서와 정적 하네스를 관리한다.

- `.github/`: 협업 템플릿과 하네스 CI
- `docs/`: AI 저장소 경계, 개발 안내와 하네스 문서
- `harness/`: 저장소 정적 검사와 검사기 자체 테스트
- `scripts/`: 로컬과 CI의 공통 검사 명령
- `src/bareum_ai/`: AI 실행 코드 (Python 3.14)
  - `dataset/`: 데이터셋 구축 도구
    - `parsing/`: 데이터셋 구축용 PDF 보고서 파서
- `tests/`: AI 실행 코드 테스트
- `pyproject.toml`, `uv.lock`: 의존성과 프로젝트 설정 (uv로 관리)

확정되지 않은 모델·프롬프트·데이터·평가 디렉터리는 미리 만들지 않는다. 실제 구현과 기준이 결정되면 해당 변경과 함께 구조와 검사를 추가한다.

## 개발 환경

[uv](https://docs.astral.sh/uv/)로 Python 3.14와 의존성을 관리한다.

```bash
uv sync   # 가상환경 생성·의존성 설치
```

의존성은 `uv add <패키지>`(개발용은 `uv add --dev <패키지>`)로 추가하고 `uv.lock`을 함께 커밋한다.

## 데이터셋 구축용 PDF 파서

`src/bareum_ai/dataset/parsing/`은 평가 데이터셋 구축에 사용하는 PyMuPDF 기반 규칙형 파서다. 서비스의 문서 입력 처리용이 아니다. 보고서 PDF에서 글자·좌표·표·도식을 추출해 문단·항목·표 구조와 원본 위치 정보를 만든다. 파싱 과정에서 LLM을 호출하지 않으며, 오류 판정과 의미 태깅은 범위에 포함하지 않는다.

```bash
uv run python -m bareum_ai.dataset.parsing.parser <PDF 파일 또는 폴더> -o <출력 폴더>
```

출력 폴더를 생략하면 현재 위치의 `parsed_output/`(Git 제외)에 저장한다. PDF마다 다음 파일을 만든다.

| 파일 | 용도 |
| --- | --- |
| `.json` | 프로그램 입력용 구조화 결과 (`schema_version` 포함) |
| `.md` | 사람이 읽는 출력 |
| `.raw.txt` | 추출 원문 비교용 |

PyMuPDF는 `dataset` 의존성 그룹으로 관리한다. 텍스트 PDF가 대상이며 OCR, 그래프 의미 해석, 모든 표·서식의 범용 복원은 보장하지 않는다. 읽기 어려운 영역은 결과에 경고로 기록한다.

## 현재 검사

```bash
./scripts/run-harness.sh check
./scripts/test-harness.sh
./scripts/test-app.sh          # AI 실행 코드 테스트 (pytest)
```

원본 PDF가 필요한 파서 테스트는 기본으로 건너뛴다. 로컬 원본이 있으면 `BAREUM_PARSER_CORPUS=<원본 폴더> ./scripts/test-app.sh`로 함께 실행한다.

현재 하네스는 필수 경로, JSON 문법, Markdown 내부 링크와 비밀정보 의심 패턴을 검사한다. 비밀정보 검사는 설정·소스·문서에서 `API_KEY`, `CLIENT_SECRET`, `SECRET`, `TOKEN`, `PASSWORD`, `PASSWD` 대입값(8자 이상)과 `sk-` 뒤 20자 이상 토큰 패턴을 찾고, `replace-me`, `example` 같은 예시값은 제외한다. 모든 비밀정보를 찾아내는 검사는 아니다. AI 결과의 정확성·성능과 서버 연동은 아직 검사하지 않는다.

## 저장소 경계

- 서버↔AI 형식은 담당자가 합의한 뒤 `bareum-server/packages/contracts/`에서 관리한다.
- AI 저장소는 합의된 계약을 소비하거나 제공하는 구현과 테스트를 담당한다.
- 다른 저장소의 문서나 파일을 복제해 별도의 기준으로 관리하지 않는다.
- 연동 검사는 계약과 실행 환경이 준비된 뒤 영향받는 저장소의 버전과 결과를 함께 기록하도록 확장한다.

## 문서

- [AI 개발 가이드](docs/guide/development-guide.md)
- [저장소 구조](docs/architecture/repository-structure.md)
- [하네스 설계 v1.4](docs/harness/harness-v1.4.md)
- [하네스 확장 로드맵](docs/harness/harness-file-roadmap.md)

## 파싱 결과 고정

[파싱 결과 고정과 라벨 연결](docs/guide/parsing-snapshots.md)에 공백/결재란 처리 정책과 고정 도구, 라벨 검증 기준을 정리했다.
