#include <stdint.h>
#include <stdio.h>
#include <sys/alt_timestamp.h>

#include "benchmark.h"
#include "resnet8.h"

uint64_t resnet8_niosv_tick(void *context);
void resnet8_niosv_report(const resnet8_benchmark_result *result);

int main(void) {
  resnet8_benchmark_result result;
  uint32_t checksum;
  int class_index;
  int rc;
  if (alt_timestamp_start() != 0) {
    printf("timestamp_start=fail\n");
    return -1;
  }
  printf("resnet8_niosv begin clock_hz=100000000\n");
  rc = resnet8_self_test(&checksum, &class_index);
  printf("self_test=%s class=%d checksum=0x%08lx\n",
         rc == 0 ? "pass" : "fail", class_index,
         (unsigned long)checksum);
  if (rc != 0) return rc;
  rc = resnet8_benchmark(resnet8_niosv_tick, 0, &result);
  if (rc != 0) return rc;
  resnet8_niosv_report(&result);
  return 0;
}
