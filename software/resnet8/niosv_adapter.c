/* Nios V HAL adapter.  Keep all BSP-specific headers out of the portable
   benchmark and inference sources. */
#include <stdint.h>
#include <stdio.h>
#include <sys/alt_timestamp.h>

#include "benchmark.h"

uint64_t resnet8_niosv_tick(void *context) {
  (void)context;
  return (uint64_t)alt_timestamp();
}

static void print_seconds(uint64_t ticks) {
  uint32_t frequency = alt_timestamp_freq();
  uint64_t whole;
  uint32_t micros;
  if (frequency == 0u) {
    printf("elapsed_seconds=unavailable\n");
    return;
  }
  whole = ticks / frequency;
  micros = (uint32_t)(((ticks % frequency) * UINT64_C(1000000)) / frequency);
  printf("elapsed_seconds=%llu.%06lu\n",
         (unsigned long long)whole, (unsigned long)micros);
}

void resnet8_niosv_report(const resnet8_benchmark_result *result) {
  printf("class=%d checksum=0x%08lx ticks=%llu warmups=%d timed=%d\n",
         result->output_class, (unsigned long)result->output_checksum,
         (unsigned long long)result->elapsed_ticks, result->warmups,
         result->timed_iterations);
  print_seconds(result->elapsed_ticks);
  printf("token_metric=not_exposed\n");
}
