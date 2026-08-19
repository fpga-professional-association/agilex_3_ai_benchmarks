#ifndef RESNET8_BENCHMARK_H
#define RESNET8_BENCHMARK_H

#include <stdint.h>

typedef uint64_t (*resnet8_tick_fn)(void *context);

typedef struct {
  uint32_t output_checksum;
  int output_class;
  uint64_t elapsed_ticks;
  int warmups;
  int timed_iterations;
} resnet8_benchmark_result;

/* The adapter owns timing and printing.  This file has no HAL/BSP includes. */
int resnet8_benchmark(resnet8_tick_fn tick, void *context,
                      resnet8_benchmark_result *result);

#endif
