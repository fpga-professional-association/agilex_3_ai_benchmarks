#include "benchmark.h"

#include "resnet8.h"

int resnet8_benchmark(resnet8_tick_fn tick, void *context,
                      resnet8_benchmark_result *result) {
  int8_t input[RESNET8_INPUT_BYTES];
  int8_t output[RESNET8_OUTPUT_CLASSES];
  uint64_t start, finish;
  int i, cls;
  if (!tick || !result) return -1;
  resnet8_make_synthetic_input(input);
  for (i = 0; i < 5; ++i) resnet8_infer(input, output);
  start = tick(context);
  for (i = 0; i < 20; ++i) resnet8_infer(input, output);
  finish = tick(context);
  cls = 0;
  for (i = 1; i < RESNET8_OUTPUT_CLASSES; ++i)
    if (output[i] > output[cls]) cls = i;
  result->output_checksum = resnet8_checksum(output, RESNET8_OUTPUT_CLASSES);
  result->output_class = cls;
  result->elapsed_ticks = finish - start;
  result->warmups = 5;
  result->timed_iterations = 20;
  return 0;
}
