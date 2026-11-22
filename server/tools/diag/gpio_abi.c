/* Prints the GPIO uAPI v2 layouts from the kernel headers, to check picar_server/hal/gpio_cdev.py against.
 *   docker run --rm -v "$PWD/server/tools/diag:/src" gcc:14 sh -c 'gcc /src/gpio_abi.c -o /tmp/a && /tmp/a'
 * Output on 2026-11-22 (gcc:14, linux-libc-dev of Debian trixie) is in tests/test_gpio_cdev.py. */
#include <stdio.h>
#include <stddef.h>
#include <linux/gpio.h>

#define OFF(t, f) (unsigned)offsetof(struct t, f)

int main(void) {
    printf("sizes request=%zu config=%zu event=%zu values=%zu line_info=%zu chip_info=%zu\n",
           sizeof(struct gpio_v2_line_request), sizeof(struct gpio_v2_line_config),
           sizeof(struct gpio_v2_line_event), sizeof(struct gpio_v2_line_values),
           sizeof(struct gpio_v2_line_info), sizeof(struct gpiochip_info));
    printf("request consumer=%u config=%u num_lines=%u event_buffer_size=%u fd=%u\n",
           OFF(gpio_v2_line_request, consumer), OFF(gpio_v2_line_request, config),
           OFF(gpio_v2_line_request, num_lines), OFF(gpio_v2_line_request, event_buffer_size),
           OFF(gpio_v2_line_request, fd));
    printf("config num_attrs=%u attrs=%u attr_size=%zu mask=%u value=%u\n",
           OFF(gpio_v2_line_config, num_attrs), OFF(gpio_v2_line_config, attrs),
           sizeof(struct gpio_v2_line_config_attribute), OFF(gpio_v2_line_config_attribute, mask),
           OFF(gpio_v2_line_attribute, values));
    printf("event id=%u offset=%u line_seqno=%u\n", OFF(gpio_v2_line_event, id), OFF(gpio_v2_line_event, offset),
           OFF(gpio_v2_line_event, line_seqno));
    printf("line_info offset=%u chip_info label=%u lines=%u\n", OFF(gpio_v2_line_info, offset),
           OFF(gpiochip_info, label), OFF(gpiochip_info, lines));
    printf("ioctl get_line=%#lx set_config=%#lx get_values=%#lx set_values=%#lx get_lineinfo=%#lx chipinfo=%#lx\n",
           (unsigned long)GPIO_V2_GET_LINE_IOCTL, (unsigned long)GPIO_V2_LINE_SET_CONFIG_IOCTL,
           (unsigned long)GPIO_V2_LINE_GET_VALUES_IOCTL, (unsigned long)GPIO_V2_LINE_SET_VALUES_IOCTL,
           (unsigned long)GPIO_V2_GET_LINEINFO_IOCTL, (unsigned long)GPIO_GET_CHIPINFO_IOCTL);
    printf("flags input=%#llx output=%#llx rising=%#llx falling=%#llx bias_disabled=%#llx pull_up=%#llx "
           "pull_down=%#llx\n",
           (unsigned long long)GPIO_V2_LINE_FLAG_INPUT, (unsigned long long)GPIO_V2_LINE_FLAG_OUTPUT,
           (unsigned long long)GPIO_V2_LINE_FLAG_EDGE_RISING, (unsigned long long)GPIO_V2_LINE_FLAG_EDGE_FALLING,
           (unsigned long long)GPIO_V2_LINE_FLAG_BIAS_DISABLED, (unsigned long long)GPIO_V2_LINE_FLAG_BIAS_PULL_UP,
           (unsigned long long)GPIO_V2_LINE_FLAG_BIAS_PULL_DOWN);
    return 0;
}
