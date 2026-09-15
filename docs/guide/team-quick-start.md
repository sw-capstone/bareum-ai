# 팀원용 하네스 빠른 참고표

이 문서는 작업 시작부터 PR까지 필요한 명령과 검사 대응만 빠르게 확인하기 위한 문서다. 자세한 설명은 [개발 가이드](./development-guide.md)를 참고한다.

이슈·PR은 [노션 협업 규칙 문서](https://app.notion.com/p/3d804af60ca8802480d3e7d65a29b5dd)의 템플릿을 기본으로 작성한다. 이슈에는 하네스용 항목을 별도로 추가하지 않고, PR의 하네스 추적 정보는 노션 템플릿의 작성 안내 주석에 통합한다.

이슈 유형은 `Bug`, `Refactor`, `Docs`, `Feature`, `Chore` 중 작업 성격에 맞게 선택한다. 성능 작업은 우선 `Refactor`와 `perf` 라벨을 사용한다. 현재 `CODEOWNERS`는 사용하지 않으며, PR 작성자가 필요한 리뷰어를 직접 지정한다.

## 1. 작업할 저장소 선택

| 작업 | 저장소 | 푸시 전 검사 |
| --- | --- | --- |
| 백엔드·AI·서버 문서·하네스 | `bareum-server` | `./scripts/run-harness.sh check`와 `./scripts/test-harness.sh` |
| 프론트엔드 | `bareum-web` | `npm run check` |

## 2. 브랜치 만들기

```bash
git switch develop
git pull --ff-only origin develop
git switch -c <작업-브랜치-이름>
```

- 작업 시작 전에 이슈 번호와 대상 저장소를 확인한다.
- 브랜치 전환·pull·merge 전에 로컬 작업이 남아 있는지 확인할 필요가 있으면 `git status --short`를 실행한다. 이 명령은 현재 로컬 상태를 확인하는 용도이며, 원격 팀원의 작업을 확인하는 명령은 아니다.
- 원격 변경이 필요하면 작업 시작 시 최신 `develop`을 받고, 진행 중인 브랜치에는 `git fetch origin` 후 기준 브랜치를 merge한다. 작업 범위가 겹칠 때만 관련 GitHub 이슈·PR을 추가로 확인하며, 모든 원격 브랜치를 비교할 필요는 없다.
- 커밋 이력 정리가 필요하면 첫 푸시 전 개인 로컬 브랜치에서만 rebase할 수 있다. 원격에 공유한 뒤에는 rebase·강제 푸시를 하지 않고, `git fetch origin` 후 `git merge origin/develop`으로 기준 브랜치를 반영한다.

작업 시작 전:

```bash
git switch develop
git pull --ff-only origin develop
```

작업 중 기준 브랜치 반영:

```bash
git fetch origin
git merge origin/develop
```

## 3. 코드 또는 문서 작업 후 검사

서버 저장소:

```bash
./scripts/run-harness.sh check
./scripts/test-harness.sh
```

웹 저장소:

```bash
npm run check
```

서버 검사 결과는 `harness/reports/report.json`에서도 확인할 수 있다.

| 상태 | 의미 |
| --- | --- |
| `passed` | 검사가 실행되어 통과함 |
| `failed` | 검사가 실행되어 실패함 |
| `not_applicable` | 검사 코드는 있지만 대상 자산이 없어 실행하지 않음. 통과를 뜻하지 않음 |
| `disabled` | 정책에서 검사가 비활성화됨 |

## 4. 실패했을 때

1. 실패한 검사 이름, 파일, 메시지를 확인한다.
2. 코드·문서·링크 등 작업 내용의 문제라면 해당 작성자가 수정한다.
3. 검사 결과가 실제 파일과 맞지 않는 등 검사 로직 문제라면 재현 명령과 결과를 하네스 담당자에게 전달한다.
4. 같은 브랜치에서 다시 검사하고 커밋·푸시한다.

하네스는 문제를 검사하고 결과를 남기지만 담당자의 코드를 대신 수정하지 않는다.

## 5. 커밋·푸시·PR

```bash
git status --short
git add <변경한 파일>
git commit -m "<변경 목적이 드러나는 메시지>"
git push -u origin <현재-브랜치>
```

PR을 열기 전에 확인한다.

- [ ] 대상 브랜치와 이슈 연결이 맞다.
- [ ] 변경 내용과 테스트·검증 결과를 노션 기준 PR 템플릿에 적었다.
- [ ] 미실행 검사나 호환성·개인정보·권한 영향, 남은 위험이 있다면 PR 본문에 적었다.
- [ ] 로컬 검사가 통과했다.
- [ ] 푸시 후 GitHub Actions 결과를 확인했다.
- [ ] 실패하면 수정 후 다시 푸시하고, 리뷰가 끝난 뒤 병합한다.

작업 브랜치는 최소 1명의 승인과 필수 검사를 통과한 뒤 `develop`에 Squash merge한다. 릴리스는 팀 전체가 검토하고 동의한 뒤 `develop`을 `main`에 Merge commit으로 병합한다. `main`과 `develop`에는 직접 푸시하지 않으며 Auto-merge와 강제 푸시는 사용하지 않는다.

## 더 읽기

- [상세 개발 가이드](./development-guide.md)
- [하네스 파일 로드맵](../harness/harness-file-roadmap.md)
