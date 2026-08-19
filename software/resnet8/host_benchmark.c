/* Optional host harness.  Nios V firmware supplies the same tick callback
   from its HAL timer and a UART reporter instead of using this file. */
#ifdef RESNET8_HOST_MAIN
#include <stdio.h>
#include <time.h>
#include "benchmark.h"
#include "resnet8.h"

static uint64_t host_ticks(void *unused) {
  (void)unused;
  return (uint64_t)clock();
}

int main(void) {
  resnet8_benchmark_result r;
  uint32_t self_checksum;
  int self_class;
  int8_t trace_input[RESNET8_INPUT_BYTES], trace_output[RESNET8_OUTPUT_CLASSES];
  uint32_t trace[16];
  resnet8_make_synthetic_input(trace_input);
  resnet8_infer_layer_checksums(trace_input, trace, trace_output);
  printf("layers=");
  for (self_class = 0; self_class < 16; ++self_class)
    printf("%s%08lx", self_class ? "," : "", (unsigned long)trace[self_class]);
  printf("\n");
  {
    int self_rc = resnet8_self_test(&self_checksum, &self_class);
    if (self_rc != 0) { printf("self_test=fail rc=%d\n", self_rc); return 2; }
  }
  if (resnet8_benchmark(host_ticks, 0, &r) != 0) return 1;
  printf("self_test=pass class=%d checksum=0x%08lx\n",
         self_class, (unsigned long)self_checksum);
  printf("class=%d checksum=0x%08lx ticks=%lu elapsed_seconds=%.17g warmups=%d timed=%d\n",
         r.output_class, (unsigned long)r.output_checksum,
         (unsigned long)r.elapsed_ticks,
         (double)r.elapsed_ticks / (double)CLOCKS_PER_SEC,
         r.warmups, r.timed_iterations);
  return 0;
}
#endif
