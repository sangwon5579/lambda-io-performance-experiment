# AWS Lambda I/O Experiment v1

이 프로젝트는 기존 실험을 보존한 상태에서 새로 시작하는 실험용 코드다.

- `lambda/disk/lambda_function.py`: Disk I/O 전용 Lambda
- `lambda/network/lambda_function.py`: Network 전용 Lambda
- `runner/run_experiment.py`: 메모리 변경, 동시 호출, barrier 동기화, 결과 저장
- `runner/plot_results.py`: 함수당 처리량과 Aggregate Throughput 그래프 생성
- `runner/check_setup.py`: AWS 인증과 Lambda 설정 확인
- `deployment/*.zip`: AWS 콘솔에 바로 업로드할 배포 파일

## 결과 파일

실험을 실행하면 다음 구조로 저장된다.

```text
results/
└── disk-buffered-YYYYMMDD-HHMMSS-xxxxxx/
    ├── metadata.json
    ├── raw_invocations.jsonl
    ├── errors.jsonl
    ├── rounds.csv
    └── summary.csv
```

- `raw_invocations.jsonl`: 성공한 모든 Lambda 호출의 원본 결과
- `errors.jsonl`: 호출 실패 및 throttling
- `rounds.csv`: 라운드별 실제 peak concurrency, 시작 시각 차이, 처리량
- `summary.csv`: 유효한 라운드만 사용한 조건별 요약

## 실험에서 사용하는 처리량

- 함수당 처리량: 각 Lambda가 처리한 데이터 / 해당 Lambda의 workload 시간
- Aggregate Window Throughput:
  모든 성공 호출의 데이터 합 / 최초 workload 시작부터 마지막 workload 종료까지의 시간

라운드는 다음 조건을 모두 만족해야 `VALID`가 된다.

1. 요청한 호출이 모두 성공
2. 실제 peak concurrency가 요청 concurrency와 동일
3. 모든 workload가 barrier 이후 허용된 지연 범위 내에서 시작

## 주의

`execution_environment_id`는 `/tmp`에 저장한 실행 환경 식별자다.
동일 Lambda 실행 환경의 재사용 여부를 확인할 수 있지만 물리 호스트 ID는 아니다.

## EC2/nginx 서버 baseline

Network Lambda 실험 전에 같은 리전의 별도 EC2 클라이언트에서 실행한다.

```bash
python runner/ec2_http_baseline.py \
  --url "http://SERVER_PRIVATE_OR_PUBLIC_IP/test100M.bin" \
  --concurrencies "1,2,5,10" \
  --rounds 5
```

이 결과는 단일 nginx 서버가 제공할 수 있는 함수당/전체 HTTP 처리량을 확인하기 위한 것이다.
Lambda Network 결과가 이 baseline의 Aggregate Throughput 부근에서 정체되면 서버 측 병목 가능성을 우선 검토한다.
