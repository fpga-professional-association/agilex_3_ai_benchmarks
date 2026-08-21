#include <stdint.h>

/* The addresses are also emitted in NIOSV_lab.sopcinfo.  Keep these local so
 * this small application does not depend on a generated system.h path. */
#define FPGA_AI_CSR_BASE       0x000a0000u
#define STREAM_BRIDGE_BASE     0x000a1000u
#define CPU_CLOCK_HZ           100000000u
#define JTAG_UART_BASE         0x00090048u
#define MTIME_BASE             0x00090000u

#define AI_CSR_INTERRUPT_CONTROL       512u
#define AI_CSR_DESCRIPTOR_DIAGNOSTICS  540u
#define AI_CSR_COMPLETION_COUNT         548u
#define AI_CSR_IP_RESET                552u
#define AI_CSR_READY_STREAMING_IFACE   556u
#define AI_CSR_CLOCKS_ACTIVE_LO         576u
#define AI_CSR_DEBUG_NETWORK_ADDR       592u
#define AI_CSR_DEBUG_NETWORK_VALID      596u
#define AI_CSR_DEBUG_NETWORK_DATA       600u
#define AI_CSR_LICENSE                 608u
#define AI_CSR_CORE_CLOCKS_ACTIVE_LO    636u
#define AI_CSR_START_CORE_STREAMING    644u

#define AI_DEBUG_IF9_TRANSACTION_LO 0x00800128u

#define BR_STATUS       0u
#define BR_INPUT_WORD0  1u
#define BR_INPUT_WORD1  2u
#define BR_INPUT_COMMIT 3u
#define BR_OUTPUT_WORD0 4u
#define BR_OUTPUT_WORD1 5u
#define BR_OUTPUT_WORD2 6u
#define BR_OUTPUT_WORD3 7u
#define BR_OUTPUT_LAST  8u
#define BR_INPUT_COUNT  9u
#define BR_OUTPUT_COUNT 10u
#define BR_CONTROL      11u

#define BR_STATUS_OUTPUT_PENDING (1u << 1)
#define BR_STATUS_INPUT_READY    (1u << 0)
#define BR_CONTROL_CLEAR_FLAGS   (1u << 0)
#define BR_CONTROL_CLEAR_COUNTS  (1u << 1)

#define IMAGE_WIDTH      32u
#define IMAGE_HEIGHT     32u
#define IMAGE_CHANNELS    3u
#define IMAGE_VALUES     (IMAGE_WIDTH * IMAGE_HEIGHT * IMAGE_CHANNELS)
#define INPUT_VALUES_PER_BEAT 6u
#define INPUT_BEATS      (IMAGE_VALUES / INPUT_VALUES_PER_BEAT)
#define OUTPUT_WORDS_MAX 16u
#define OUTPUT_BEATS_MAX  8u
#define TIMEOUT_CYCLES  100000000u

#define INPUT_ZERO            0u
#define INPUT_ALL_255         1u
#define INPUT_SYNTHETIC_NHWC  2u
#define INPUT_SYNTHETIC_NCHW  3u

static volatile uint32_t *const ai_csr = (volatile uint32_t *)FPGA_AI_CSR_BASE;
static volatile uint32_t *const stream_bridge = (volatile uint32_t *)STREAM_BRIDGE_BASE;
static volatile uint32_t *const jtag_uart = (volatile uint32_t *)JTAG_UART_BASE;
static uint8_t synthetic_image[IMAGE_VALUES];

static inline uint32_t clock_ticks32(void)
{
    /* Nios V/m does not implement the RDCYCLE instruction.  Its timer
     * software agent exposes the CPU-rate mtime counter at this address. */
    return *(volatile uint32_t *)MTIME_BASE;
}

static inline void ai_write(uint32_t byte_offset, uint32_t value)
{
    ai_csr[byte_offset >> 2] = value;
}

static inline uint32_t ai_read(uint32_t byte_offset)
{
    return ai_csr[byte_offset >> 2];
}

static inline void bridge_write(uint32_t word_offset, uint32_t value)
{
    stream_bridge[word_offset] = value;
}

static inline uint32_t bridge_read(uint32_t word_offset)
{
    return stream_bridge[word_offset];
}

/* Keep the stdout adapter independent of the HAL's formatted-I/O library.
 * Avalon JTAG UART control[31:16] is the number of writable FIFO entries. */
static void uart_putc(char c)
{
    while ((jtag_uart[1] >> 16) == 0u) {
    }
    jtag_uart[0] = (uint32_t)(uint8_t)c;
}

static void uart_puts(const char *text)
{
    while (*text != '\0')
        uart_putc(*text++);
}

static void uart_hex16(uint16_t value)
{
    static const char digits[] = "0123456789abcdef";
    uint32_t shift;
    for (shift = 12u; ; shift -= 4u) {
        uart_putc(digits[(value >> shift) & 0xfu]);
        if (shift == 0u)
            break;
    }
}

static void uart_hex32(uint32_t value)
{
    uart_hex16((uint16_t)(value >> 16));
    uart_hex16((uint16_t)value);
}

static void uart_dec32(uint32_t value)
{
    char digits[10];
    uint32_t count = 0u;
    if (value == 0u) {
        uart_putc('0');
        return;
    }
    while (value != 0u) {
        digits[count++] = (char)('0' + (value % 10u));
        value /= 10u;
    }
    while (count != 0u)
        uart_putc(digits[--count]);
}

static uint32_t bridge_input_ready(void)
{
    return bridge_read(BR_STATUS) & BR_STATUS_INPUT_READY;
}

static uint32_t bridge_output_pending(void)
{
    return bridge_read(BR_STATUS) & BR_STATUS_OUTPUT_PENDING;
}

static uint32_t ai_streaming_active(void)
{
    return ai_read(AI_CSR_READY_STREAMING_IFACE) != 0u;
}

static int ai_debug_read(uint32_t address, uint32_t *value)
{
    uint32_t start = clock_ticks32();
    ai_write(AI_CSR_DEBUG_NETWORK_ADDR, address);
    while (ai_read(AI_CSR_DEBUG_NETWORK_VALID) == 0u) {
        if ((uint32_t)(clock_ticks32() - start) > TIMEOUT_CYCLES)
            return -1;
    }
    *value = ai_read(AI_CSR_DEBUG_NETWORK_DATA);
    return 0;
}

static uint16_t uint8_to_half(uint32_t value)
{
    /* All values are integers in [0,255], so conversion to binary16 is exact. */
    uint32_t exponent = 0u;
    uint32_t mantissa;
    if (value == 0u)
        return 0u;
    while ((1u << (exponent + 1u)) <= value)
        ++exponent;
    mantissa = value - (1u << exponent);
    return (uint16_t)(((exponent + 15u) << 10) | (mantissa << (10u - exponent)));
}

static void initialize_synthetic_image(void)
{
    uint32_t state = 0x13579bdfu;
    uint32_t i;
    for (i = 0; i < IMAGE_VALUES; ++i) {
        state = state * 1664525u + 1013904223u;
        synthetic_image[i] = (uint8_t)((((state >> 24) ^ (i * 29u)) & 0xffu) ^ 0x80u);
    }
}

static uint32_t input_value(uint32_t input_kind, uint32_t offset)
{
    if (input_kind == INPUT_ZERO)
        return 0u;
    if (input_kind == INPUT_ALL_255)
        return 255u;
    if (input_kind == INPUT_SYNTHETIC_NCHW) {
        uint32_t channel = offset / (IMAGE_WIDTH * IMAGE_HEIGHT);
        uint32_t pixel = offset % (IMAGE_WIDTH * IMAGE_HEIGHT);
        return synthetic_image[pixel * IMAGE_CHANNELS + channel];
    }
    return synthetic_image[offset];
}

static int start_streaming(void)
{
    uint32_t start;

    /* The integrated architecture has on-chip parameters and no DDR.  In
     * this mode READY_STREAMING_IFACE starts the streaming job; no descriptor
     * write is required. */
    if (!ai_streaming_active()) {
        ai_write(AI_CSR_READY_STREAMING_IFACE, 1u);
        start = clock_ticks32();
        while (!ai_streaming_active()) {
            if ((uint32_t)(clock_ticks32() - start) > TIMEOUT_CYCLES) {
                uart_puts("ERROR DLA streaming interface did not become ready\n");
                return -1;
            }
        }
    }
    ai_write(AI_CSR_START_CORE_STREAMING, 1u);
    return 0;
}

static int send_input(uint32_t input_kind)
{
    uint32_t i;
    for (i = 0; i < IMAGE_VALUES; i += INPUT_VALUES_PER_BEAT) {
        uint16_t image0;
        uint16_t image1;
        uint16_t image2;
        uint16_t image3;
        uint16_t image4;
        uint16_t image5;
        uint32_t start = clock_ticks32();
        while (!bridge_input_ready()) {
            if ((uint32_t)(clock_ticks32() - start) > TIMEOUT_CYCLES) {
                uart_puts("ERROR input timeout beat ");
                uart_dec32(i / INPUT_VALUES_PER_BEAT);
                uart_putc('\n');
                return -1;
            }
        }
        image0 = uint8_to_half(input_value(input_kind, i));
        image1 = uint8_to_half(input_value(input_kind, i + 1u));
        image2 = uint8_to_half(input_value(input_kind, i + 2u));
        image3 = uint8_to_half(input_value(input_kind, i + 3u));
        image4 = uint8_to_half(input_value(input_kind, i + 4u));
        image5 = uint8_to_half(input_value(input_kind, i + 5u));
        /* The bridge concatenates {word2, word1, word0} into AXIS[95:0]. */
        bridge_write(BR_INPUT_WORD0, (uint32_t)image0 | ((uint32_t)image1 << 16));
        bridge_write(BR_INPUT_WORD1, (uint32_t)image2 | ((uint32_t)image3 << 16));
        bridge_write(BR_INPUT_COMMIT, (uint32_t)image4 | ((uint32_t)image5 << 16));
    }
    return 0;
}

static int drain_output(uint16_t *raw, uint32_t *word_count,
                        uint16_t *beat_strobes, uint32_t *beat_count)
{
    uint32_t words = 0;
    uint32_t beats = 0;
    for (;;) {
        uint32_t start = clock_ticks32();
        uint32_t last;
        uint32_t status;
        uint32_t strobe;
        while (!bridge_output_pending()) {
            if ((uint32_t)(clock_ticks32() - start) > TIMEOUT_CYCLES) {
                uart_puts("ERROR output timeout beat ");
                uart_dec32(beats);
                uart_putc('\n');
                return -1;
            }
        }

        status = bridge_read(BR_STATUS);
        strobe = (status >> 8) & 0xffffu;
        beat_strobes[beats] = (uint16_t)strobe;
        {
            uint32_t word0 = bridge_read(BR_OUTPUT_WORD0);
            uint32_t word1 = bridge_read(BR_OUTPUT_WORD1);
            uint32_t word2 = bridge_read(BR_OUTPUT_WORD2);
            uint32_t word3;
            uint16_t lanes[8];
            uint32_t lane;
            last = bridge_read(BR_OUTPUT_LAST) & 1u;
            word3 = bridge_read(BR_OUTPUT_WORD3); /* one read acknowledges output */
            lanes[0] = (uint16_t)word0;
            lanes[1] = (uint16_t)(word0 >> 16);
            lanes[2] = (uint16_t)word1;
            lanes[3] = (uint16_t)(word1 >> 16);
            lanes[4] = (uint16_t)word2;
            lanes[5] = (uint16_t)(word2 >> 16);
            lanes[6] = (uint16_t)word3;
            lanes[7] = (uint16_t)(word3 >> 16);
            for (lane = 0; lane < 8u; ++lane) {
                uint32_t lane_mask = 3u << (lane * 2u);
                if ((strobe & lane_mask) == lane_mask) {
                    if (words >= OUTPUT_WORDS_MAX) {
                        uart_puts("ERROR output exceeded capacity\n");
                        return -1;
                    }
                    raw[words++] = lanes[lane];
                } else if ((strobe & lane_mask) != 0u) {
                    uart_puts("ERROR partial FP16 output strobe\n");
                    return -1;
                }
            }
        }
        ++beats;
        if (last)
            break;
        if (beats >= OUTPUT_BEATS_MAX) {
            uart_puts("ERROR output did not assert TLAST\n");
            return -1;
        }
    }
    *word_count = words;
    *beat_count = beats;
    return 0;
}

static int run_inference(uint32_t input_kind, uint16_t *raw,
                         uint32_t *word_count, uint16_t *beat_strobes,
                         uint32_t *beat_count, uint32_t *cycles)
{
    uint32_t begin = clock_ticks32();
    if (send_input(input_kind) != 0 ||
        drain_output(raw, word_count, beat_strobes, beat_count) != 0)
        return -1;
    *cycles = (uint32_t)(clock_ticks32() - begin);
    return 0;
}

static uint32_t output_class(const uint16_t *raw, uint32_t words);

static void print_output(const char *name, const uint16_t *raw, uint32_t words,
                         const uint16_t *beat_strobes, uint32_t beats)
{
    uint32_t i;
    uart_puts("case=");
    uart_puts(name);
    uart_puts(" raw_words=");
    uart_dec32(words);
    uart_puts(" strobes=");
    for (i = 0; i < beats; ++i) {
        if (i != 0u)
            uart_putc(',');
        uart_hex16(beat_strobes[i]);
    }
    uart_puts(" raw:");
    for (i = 0; i < words; ++i) {
        uart_putc(' ');
        uart_hex16(raw[i]);
    }
    uart_puts(" class=");
    uart_dec32(output_class(raw, words));
    uart_putc('\n');
}

static uint16_t half_order_key(uint16_t value)
{
    return (value & 0x8000u) ? (uint16_t)~value : (uint16_t)(value ^ 0x8000u);
}

static uint32_t output_class(const uint16_t *raw, uint32_t words)
{
    uint32_t count = words < 10u ? words : 10u;
    uint32_t best = 0;
    uint32_t i;
    for (i = 1; i < count; ++i) {
        if (half_order_key(raw[i]) > half_order_key(raw[best]))
            best = i;
    }
    return best;
}

int main(void)
{
    uint16_t raw[OUTPUT_WORDS_MAX];
    uint32_t words = 0;
    uint16_t beat_strobes[OUTPUT_BEATS_MAX];
    uint32_t beats = 0;
    uint32_t cycles = 0;
    uint32_t i;
    uint32_t min_cycles = 0xffffffffu;
    uint32_t max_cycles = 0u;
    uint32_t total_cycles = 0u;
    uint32_t completion_before;
    uint32_t core_clocks_before;
    uint32_t active_clocks_before;
    uint32_t if9_transactions_before;
    uint32_t if9_transactions_after;

    uart_puts("fpga_ai_resnet8 start csr=0x");
    uart_hex32(FPGA_AI_CSR_BASE);
    uart_puts(" bridge=0x");
    uart_hex32(STREAM_BRIDGE_BASE);
    uart_puts(" clock=");
    uart_dec32(CPU_CLOCK_HZ);
    uart_putc('\n');
    initialize_synthetic_image();
    bridge_write(BR_CONTROL, BR_CONTROL_CLEAR_FLAGS | BR_CONTROL_CLEAR_COUNTS);
    if (start_streaming() != 0)
        return 1;

    completion_before = ai_read(AI_CSR_COMPLETION_COUNT);
    core_clocks_before = ai_read(AI_CSR_CORE_CLOCKS_ACTIVE_LO);
    active_clocks_before = ai_read(AI_CSR_CLOCKS_ACTIVE_LO);
    if (ai_debug_read(AI_DEBUG_IF9_TRANSACTION_LO,
                      &if9_transactions_before) != 0) {
        uart_puts("ERROR debug profiler pre-read timeout\n");
        return 1;
    }

    if (run_inference(INPUT_ZERO, raw, &words, beat_strobes, &beats, &cycles) != 0)
        return 1;
    print_output("zero", raw, words, beat_strobes, beats);
    if (ai_debug_read(AI_DEBUG_IF9_TRANSACTION_LO,
                      &if9_transactions_after) != 0) {
        uart_puts("ERROR debug profiler post-read timeout\n");
        return 1;
    }
    uart_puts("zero_progress input_beats=");
    uart_dec32(bridge_read(BR_INPUT_COUNT));
    uart_puts(" output_beats=");
    uart_dec32(bridge_read(BR_OUTPUT_COUNT));
    uart_puts(" completions_delta=");
    uart_dec32(ai_read(AI_CSR_COMPLETION_COUNT) - completion_before);
    uart_puts(" active_clocks_delta=");
    uart_dec32(ai_read(AI_CSR_CLOCKS_ACTIVE_LO) - active_clocks_before);
    uart_puts(" core_clocks_delta=");
    uart_dec32(ai_read(AI_CSR_CORE_CLOCKS_ACTIVE_LO) - core_clocks_before);
    uart_puts(" if9_transactions_delta=");
    uart_dec32(if9_transactions_after - if9_transactions_before);
    uart_putc('\n');
    if (run_inference(INPUT_ALL_255, raw, &words, beat_strobes, &beats, &cycles) != 0)
        return 1;
    print_output("all255", raw, words, beat_strobes, beats);
    if (run_inference(INPUT_SYNTHETIC_NHWC, raw, &words, beat_strobes, &beats, &cycles) != 0)
        return 1;
    print_output("synthetic_nhwc_stream", raw, words, beat_strobes, beats);
    if (run_inference(INPUT_SYNTHETIC_NCHW, raw, &words, beat_strobes, &beats, &cycles) != 0)
        return 1;
    print_output("synthetic_nchw_stream", raw, words, beat_strobes, beats);

    for (i = 0; i < 5u; ++i) {
        if (run_inference(INPUT_SYNTHETIC_NHWC, raw, &words,
                          beat_strobes, &beats, &cycles) != 0)
            return 1;
    }
    for (i = 0; i < 20u; ++i) {
        if (run_inference(INPUT_SYNTHETIC_NHWC, raw, &words,
                          beat_strobes, &beats, &cycles) != 0)
            return 1;
        if (cycles < min_cycles)
            min_cycles = cycles;
        if (cycles > max_cycles)
            max_cycles = cycles;
        total_cycles += cycles;
    }

    print_output("timed_synthetic_nhwc_stream", raw, words,
                 beat_strobes, beats);
    uart_puts("class=");
    uart_dec32(output_class(raw, words));
    uart_puts(" output_beats=");
    uart_dec32((words + 7u) / 8u);
    uart_putc('\n');
    uart_puts("warmups=5 timed=20 cycles_min=");
    uart_dec32(min_cycles);
    uart_puts(" cycles_max=");
    uart_dec32(max_cycles);
    uart_puts(" cycles_avg=");
    uart_dec32(total_cycles / 20u);
    uart_puts(" latency_us_avg=");
    uart_dec32((total_cycles / 20u) / (CPU_CLOCK_HZ / 1000000u));
    uart_putc('\n');
    uart_puts("bridge_status=0x");
    uart_hex32(bridge_read(BR_STATUS));
    uart_puts(" input_tail=");
    uart_hex32(bridge_read(BR_INPUT_WORD0));
    uart_putc(',');
    uart_hex32(bridge_read(BR_INPUT_WORD1));
    uart_putc(',');
    uart_hex32(bridge_read(BR_INPUT_COMMIT));
    uart_puts(" license=0x");
    uart_hex32(ai_read(AI_CSR_LICENSE));
    uart_puts(" irq=0x");
    uart_hex32(ai_read(AI_CSR_INTERRUPT_CONTROL));
    uart_puts(" diagnostics=0x");
    uart_hex32(ai_read(AI_CSR_DESCRIPTOR_DIAGNOSTICS));
    uart_puts(" completion_count=");
    uart_dec32(ai_read(AI_CSR_COMPLETION_COUNT));
    uart_puts(" bridge_counts=");
    uart_dec32(bridge_read(BR_INPUT_COUNT));
    uart_putc(',');
    uart_dec32(bridge_read(BR_OUTPUT_COUNT));
    uart_putc('\n');
    return 0;
}
