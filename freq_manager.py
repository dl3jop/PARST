#!/usr/bin/env python3
class FreqManager:
	# data from satnogs json
    sat_uplink_low = 0;
    sat_uplink_high = 0;
    sat_downlink_low = 0;
    sat_downlink_high = 0;
    sat_is_linear = 0;
    sat_bandwidth = 0;
    
    # currently applied RIT
    rit = 0;
    
    # Frequencies after doppler correction, RIT not applied
    current_uplink = 0;
    current_downlink = 0;
    
    # Current selected TPX of Sat
    current_tpx = 0;
    current_demod = 0;
    
    # doppler shifts
    doppler_up = 0;
    doppler_down = 0;
    
    # Current offset from TPX middle  
    current_tpx_offset = 0;
    
    # TPX inversion type
    current_tpx_inversion = 1;
    
    
    # Current HW frequency
    current_rig_uplink = 0;
    current_rig_downlink = 0;
    
    old_rig_uplink = 0;
    old_rig_downlink = 0;
    
    
    # SDR PLL frequency
    sdr_pll_freq = 145500000;
    # difference PLL to RX frequency 
    sdr_shift = 0;
    sdr_samplerate = 2400000;
