#!/usr/bin/env python3

### SDR commands

### rtl_tcp + nmux + csdr -> heavy but allows multiple commands in parallel
rtl_tcp_command = "rtl_tcp -a 127.0.0.1 -s 2.4M -p 4950 -f 145.5M -g 20 "
csdr_lsb_command = "bash -c \"(for anything in {0..10}; do ncat 127.0.0.1 4952; sleep .3; done) | csdr convert_u8_f | csdr shift_addition_cc --fifo sdr_rx_tune_pipe | csdr fir_decimate_cc 50 0.005 HAMMING | csdr bandpass_fir_fft_cc -0.1 0 0.05 | csdr realpart_cf | csdr agc_ff | csdr limit_ff | csdr convert_f_s16 | play -r 48000 -t s16 -L -c 1 --multi-threaded - \""
iq_mux_command = "bash -c \"(for anything in {0..10}; do ncat 127.0.0.1 4950; sleep .3; done) | nmux -p 4952 -a 127.0.0.1 -b 1024 -n 30\""

### better for older hardware like Raspberry Pi 2 and alike 
## FM
sdr_simple_command = "bash -c \"rtl_udp -F -f 144500000 -s 48000 -R -g 20 - | csdr convert_s16_f | csdr fmdemod_quadri_cf | csdr limit_ff | csdr deemphasis_nfm_ff 48000 | csdr fastagc_ff | csdr convert_f_s16 | play -r 48000 -t s16 -L -c 1 --multi-threaded -\""

### hybrid version
sdr_hybrid_iq = "bash -c \"rtl_udp -F -f 144500000 -s 48000 -R -C -g 20 - | csdr convert_s16_f| nmux -p 4952 -a 127.0.0.1 -b 1024 -n 30 \""
sdr_hybrid_command_usb = "bash -c \"nc -v localhost 4952 |csdr bandpass_fir_fft_cc 0.01 0.17 0.002 | csdr realpart_cf | csdr agc_ff | csdr limit_ff | csdr convert_f_s16 | play -r 48000 -t s16 -L -c 1 --multi-threaded -\""
sdr_hybrid_command_lsb = "bash -c \"nc -v localhost 4952 |csdr bandpass_fir_fft_cc -0.17 -0.01 0.002 | csdr realpart_cf | csdr agc_ff | csdr limit_ff | csdr convert_f_s16 | play -r 48000 -t s16 -L -c 1 --multi-threaded -\""
sdr_hybrid_command_nfm = "bash -c \"nc -v localhost 4952 |csdr fmdemod_quadri_cf | csdr limit_ff | csdr deemphasis_nfm_ff 48000 | csdr fastagc_ff | csdr convert_f_s16 | play -r 48000 -t s16 -L -c 1 --multi-threaded -\""
sdr_hybrid_command_mute = ""

demod_list = "NFM", "LSB", "USB", "Mute"
demod_command_list = [sdr_hybrid_command_nfm, sdr_hybrid_command_lsb, sdr_hybrid_command_usb, sdr_hybrid_command_mute]
##

  
def update_freq_simple_demod(freq):
    data = "" +chr(0)
    i = 0
    freq = int(freq*(1e6))
    # print("Updated sdr demod")
    while i < 4:
        data = data +chr(freq & 0xff)
        freq = freq >> 8
        i=i+1
    s.send(six.b(data))
        
def update_demod_simple_demod(demod):
    data = "" +chr(1)
    print("## Updated sdr demod")
    i = 0
    while i < 4:
        data = data +chr(demod & 0xff)
        demod = demod >> 8
        i=i+1
    # s.send(six.b(data))
 
