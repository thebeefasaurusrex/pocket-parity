#include <gb/gb.h>
#include <stdint.h>

#define READY (*(volatile uint8_t *)0xc100)
#define SECONDS (*(volatile uint8_t *)0xc101)
#define PHASE (*(volatile uint8_t *)0xc102)
#define X (*(volatile uint8_t *)0xc103)
#define A_FRAMES (*(volatile uint8_t *)0xc104)
#define TICK (*(volatile uint8_t *)0xc105)

static const uint8_t tiles[] = {
    0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,
    255,255,129,129,129,129,129,129,129,129,129,129,129,129,255,255,
    255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255
};
static const uint8_t background[] = {1, 2, 1, 2};

void main(void) {
    DISPLAY_OFF;
    READY = 0;
    SECONDS = PHASE = A_FRAMES = TICK = 0;
    X = 24;
    set_bkg_data(0, 3, tiles);
    set_sprite_data(0, 3, tiles);
    fill_bkg_rect(0, 0, 32, 32, 0);
    set_bkg_tiles(0, 0, 2, 2, background);
    set_sprite_tile(0, 2);
    set_sprite_prop(0, 0);
    move_sprite(0, X, 40);
    SHOW_BKG;
    SHOW_SPRITES;
    DISPLAY_ON;
    while (1) {
        uint8_t buttons;
        vsync();
        buttons = joypad();
        if (buttons & J_START) {
            SECONDS = PHASE = A_FRAMES = TICK = 0;
            X = 24;
            READY = 1;
        } else if (READY) {
            if (++TICK == 60) {
                TICK = 0;
                if (++SECONDS == 35) PHASE = 1;
            }
            if (buttons & J_RIGHT) ++X;
            if (buttons & J_A) ++A_FRAMES;
        }
        move_sprite(0, X, 40);
    }
}
