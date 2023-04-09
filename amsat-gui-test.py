#!/usr/bin/env python3

# Config parser for external config file
import configparser

# For bash calls
import sys
import os
# Time mangement
import datetime
import time
# Interface for pyepehm
import pysattracker.sattracker as sattracker
# JSON to process SatNogs transponder file
import json
# Communication with rtl-udp
import socket
import six
import struct
# Communication with Rig
import serial
import Hamlib

import signal

### Load config file
config_file = configparser.ConfigParser(converters={'list': lambda x: [i.strip() for i in x.split(',')]})
config_file.sections()
try:
    with open('amsat-gui.config') as f:
        config_file.read_file(f)
except IOError:
    print("Config file missing!")
    exit()


if (config_file['UI']['qt_version'] == "6"):
    # Import PyQt6 stuff
    from PyQt6 import QtWidgets, uic, QtGui
    from PyQt6.QtWidgets import *
    from PyQt6.QtGui import * 
    from PyQt6.QtCore import Qt,QTimer,QDateTime,QObject, QThread, pyqtSignal, QProcess, QSize
    from qt_material import apply_stylesheet
elif (config_file['UI']['qt_version'] == "5"):
    # Import PyQt5 stuff
    from PyQt5 import QtWidgets, uic, QtGui
    from PyQt5.QtWidgets import *
    from PyQt5.QtGui import * 
    from PyQt5.QtCore import Qt,QTimer,QDateTime,QObject, QThread, pyqtSignal, QProcess, QSize
    from qt_material import apply_stylesheet
else:
    print("Unsupported PyQt Version:" + (config_file['UI']['qt_version']) + " -> Use PyQt5 or PyQt6")
    exit()

import matplotlib
matplotlib.use('Qt5Agg')
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
from matplotlib.figure import Figure
import matplotlib.pyplot as plt
import numpy as np
from math import radians
    
    
  
### Setup QTH and Satellite information
qth = (config_file['QTH']['latitude'],config_file['QTH']['longitude'],config_file['QTH']['elevation'])
wanted_sats = config_file['SATS'].getlist('sat_list')

### check if the system is a rpi ## needs to be fixed
is_rpi = 0
if (os.uname()[1] == 'raspberrypi'):
    print("It's a PI")
    import RPi.GPIO as GPIO
    from rpi_rotary_encoder.encoder import Encoder
    GPIO.setmode(GPIO.BCM)
    is_rpi = 1

### which demodulator chain should be used?
demod_chain = config_file['SDR']['demod_chain'];


### Setting up pipe to control shift for csdr
csdr_shift_fifo_path = 'sdr_rx_tune_pipe'

if(demod_chain == "0"):
    try:
        os.remove(csdr_shift_fifo_path)
    except:
        pass
    time.sleep(1)
    try:
        os.mkfifo(csdr_shift_fifo_path)
    except:
        print("Error creating tuning FIFO, aborting...")
        exit()
    
    time.sleep(1)
    try:
        csdr_shift_fifo_file = os.open(csdr_shift_fifo_path, os.O_RDWR)
    except:
        print("Error opening FIFO, exiting...")
  

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


s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

nmux_proc = QProcess()
demod_proc = QProcess()
rtl_tcp_proc = QProcess()
sdr_demod_simple_proc = QProcess()

sdr_hybrid_iq_proc = QProcess()
sdr_hybrid_demod_proc = QProcess()

# Halmlib config
Hamlib.rig_set_debug(Hamlib.RIG_DEBUG_NONE)
tx_rig = Hamlib.Rig(Hamlib.RIG_MODEL_FT818)
tx_rig.set_conf("rig_pathname", "/dev/ttyUSB0")
tx_rig.set_conf("retry", "5")


# satellite tle and transponder files
tle_file_path = "amsat-tle.txt"
json_file_path = "satnogs.json"



# split TLE into triple lists
def triple(l, n):
  return [l[i:i+n] for i in range(0, len(l), n)]
  
  
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
    
# Catalog with Satellite Data
class SatCatalog:
    tracker_list=[]
    name_list=[]
    id_list=[]
    tpx_list=[[]]
    tpx_freq_list=[[]]
    current_sat = 0
    current_sat_azi = 0;
    current_sat_ele = 0;
	
mySats = SatCatalog()


### frequency management
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
    
myFreqs = FreqManager()


### rig manager
class RigManager:
    rig_name_uplink = "FT818"
    rig_name_downlink = "SDR"
    rig_uplink_connected = 0
    rig_downlink_connected = 0
    trigger_uplink_changed = 0

myRig = RigManager()



### TLE file 
tle_file = open(tle_file_path, 'r')
if tle_file == None:
    print("TLE file missing")
    exit()
tle_triple = triple(tle_file.readlines(), 3)

### TPX file from satnogs
tpx_file = open(json_file_path, 'r')
if tpx_file == None:
    print("TPX file missing")
    exit()
tpx_data = json.loads(tpx_file.read())


try:
    tx_rig.open()
    myRig.rig_uplink_connected = 1
except:
    myrig.rig_uplink_connected = 0

class uplink_tx(QObject):
    
    
    def __init__(self):
        super().__init__()
        print("Uplink TX Init")
    
    def run(self):
        while 1:
            diff = abs(int(myFreqs.current_rig_uplink*1e6)-int(myFreqs.old_rig_uplink*1e6))
            if myRig.trigger_uplink_changed == 1 and diff >= 10 and int(myFreqs.current_rig_uplink) > 1:
                myFreqs.old_rig_uplink = myFreqs.current_rig_uplink
                tx_rig.set_freq(Hamlib.RIG_VFO_A,float(int(myFreqs.current_rig_uplink*1e6)))
                myRig.trigger_uplink_changed = 0
            time.sleep(0.4)
                
class sdr_rx(QObject):

    def __init__(self):
        super(sdr_rx, self).__init__()
        print("initialising sdr...")
        if(demod_chain == "0"):
            rtl_tcp_proc.readyReadStandardOutput.connect(self.rtl_output)
            nmux_proc.readyReadStandardOutput.connect(self.nmux_output)
            demod_proc.readyReadStandardOutput.connect(self.demod_output)
            
            rtl_tcp_proc.start(rtl_tcp_command)
            time.sleep(1)
            nmux_proc.start(iq_mux_command)
            time.sleep(1)
            demod_proc.start(csdr_lsb_command)
            time.sleep(3)
        elif(demod_chain == "1"):
            print("Starting simple demod")
            sdr_demod_simple_proc.readyReadStandardError.connect(self.simple_demod_stderr)
            #sdr_demod_simple_proc.readyReadStandardOutput.connect(self.simple_demod_stdout)
            sdr_demod_simple_proc.start(sdr_simple_command)
            
        elif(demod_chain == "2"):
            print("Starting hybrid demod")
            sdr_hybrid_iq_proc.readyReadStandardError.connect(self.hybrid_iq_stderr)
            sdr_hybrid_iq_proc.start(sdr_hybrid_iq)
            sdr_hybrid_demod_proc.readyReadStandardError.connect(self.hybrid_demod_stderr)
            sdr_hybrid_demod_proc.start(demod_command_list[myFreqs.current_demod])

    def rtl_output(self):
        data = rtl_tcp_proc.readAllStandardOutput()
        stdout = bytes(data).decode("utf8")
        print("RTL_TCP: " + stdout)
        print("###")
		
    def nmux_output(self):
        data = nmux_proc.readAllStandardOutput()
        stdout = bytes(data).decode("utf8")
        print("NMUX: " + stdout)
        print("###")
		
    def demod_output(self):
        data = demod_proc.readAllStandardOutput()
        stdout = bytes(data).decode("utf8")
        print("DEMOD: " + stdout)
        print("###")
        
    def simple_demod_stderr(self):
        data = sdr_demod_simple_proc.readAllStandardError()
        stdout = bytes(data).decode("utf8")
        print("DEMOD: " + stdout)
    def simple_demod_stdout(self):
        data = sdr_demod_simple_proc.readAllStandardOutput()
        stdout = bytes(data).decode("utf8")
        print("DEMOD: " + stdout)
        print("###")
    def hybrid_iq_stderr(self):
        data = sdr_hybrid_iq_proc.readAllStandardError()
        stdout = bytes(data).decode("utf8")
        print("DEMOD: " + stdout)
    def hybrid_iq_stdout(self):
        data = sdr_hybrid_iq_proc.readAllStandardOutput()
        stdout = bytes(data).decode("utf8")
        print("DEMOD: " + stdout)
        print("###")
    def hybrid_demod_stderr(self):
        data = sdr_hybrid_demod_proc.readAllStandardError()
        stdout = bytes(data).decode("utf8")
        print("DEMOD: " + stdout)
    def hybrid_demod_stdout(self):
        data = sdr_hybrid_demod_proc.readAllStandardOutput()
        stdout = bytes(data).decode("utf8")
        print("DEMOD: " + stdout)
        print("###")
        
    def change_demod_hybrid():
        print("Demod:" + str(myFreqs.current_demod))
        sdr_hybrid_demod_proc.terminate()
        sdr_hybrid_demod_proc.waitForFinished()

        sdr_hybrid_demod_proc.start(demod_command_list[myFreqs.current_demod])
    
    ### runtime sanity check to be implemented    
    def run(self):
        while 1:
            #Here is a good place for YOUR error handling ^^
            time.sleep(1)
        

class Ui(QtWidgets.QMainWindow):
    def resizeEvent(self, event):
        QtWidgets.QMainWindow.resizeEvent(self, event)
        #self.polarplot_widget.resize((int(self.current_pass_tab.width()/2),self.current_pass_tab.height()-50))
        self.polarplot_widget.setMaximumHeight(self.current_pass_tab.height())
        self.polarplot_widget.setMaximumWidth(int(self.current_pass_tab.width()/2))
        self.polarplot_widget.setMinimumHeight(self.current_pass_tab.height())
        self.polarplot_widget.setMinimumWidth(int(self.current_pass_tab.width()/2))
	### Update Ele/Azi labels
    def update_sat_ele(self,text):
        self.satellite_elevation.setText(text)
    def update_sat_azi(self,text):
        self.satellite_azimuth.setText(text)
        
    # encoder callbacks
    def valueChanged_VFO(self,value, direction):
        myFreqs.current_tpx_offset = value *50
        self.slider_tpx.setValue(myFreqs.current_tpx_offset)
        self.update_rig_frequencies()
        self.update_frequency_labels()
        
    def valueChanged_RIT(self,value, direction):
        myFreqs.rit = value*10
        self.update_rig_frequencies()
        self.update_frequency_labels()
        pass  
    
     
    ### Parsing staellites from TLE which are wanted into sat list   
    def get_satellites(self):
        for sat in tle_triple:
            if sat[0].replace('\n', "") in wanted_sats:
                #print("Added satellite: " + sat[0].replace('\n', "") + " to database with catalog id: " + str(int(sat[1].replace('\n', "")[2:7])))
                sat_tle_list = {"name":sat[0].replace('\n', ""), "tle1":sat[1].replace('\n', ""), "tle2":sat[2].replace('\n', "")}
                mySats.tracker_list.append(sattracker.Tracker(sat_tle_list, groundstation=qth))
                mySats.name_list.append(sat[0].replace('\n', ""))
                mySats.id_list.append(int(sat[1].replace('\n', "")[2:7]))
    
    ### Get TPX for every Sat in list            
    def get_modes(self):
        for index, tracker in enumerate(mySats.tracker_list):
            query = list(filter(lambda x:x["norad_cat_id"]==mySats.id_list[index],tpx_data))
            for i in range(0,len(query),1):
                mySats.tpx_list[index].append([query[i]["description"]])
                mySats.tpx_freq_list[index].append([query[i]["uplink_low"],query[i]["uplink_high"],query[i]["downlink_low"],query[i]["downlink_high"],query[i]["invert"]])
            mySats.tpx_list.append([])
            mySats.tpx_freq_list.append([])
    
    
    ### Updates sat selection box
    def comboboxSAT_set_entries(self):
        for index, tracker in enumerate(mySats.tracker_list):
            #print("Added Sat to combo: " + mySats.name_list[index])
            self.satellite_selector.addItem(mySats.name_list[index])
     
    ### adds modes to mode selection for selected sat 
    def comboboxMode_set_entries(self,index):
        self.mode_selector.clear()
        mySats.current_sat = index
        #print("Current sat is: " + str(mySats.current_sat))
        for mode in mySats.tpx_list[index]:
            self.mode_selector.addItem(str("".join(mode)))
        myFreqs.current_tpx = 0;
            
    ### Updates current tpx on selection
    def update_selected_tpx(self,index):
        myFreqs.current_tpx = index;
        self.update_frequencies(index)
    
    def update_selected_demod(self,index):
        myFreqs.current_demod = index;
        sdr_rx.change_demod_hybrid()
    
    
    ### get frequencies from tpx database and get tpx mode
    def update_frequencies(self,index_tpx):
        myFreqs.sat_uplink_low = (mySats.tpx_freq_list[mySats.current_sat][index_tpx])[0]
        myFreqs.sat_uplink_high = (mySats.tpx_freq_list[mySats.current_sat][index_tpx])[1]
        myFreqs.sat_downlink_low = (mySats.tpx_freq_list[mySats.current_sat][index_tpx])[2]
        myFreqs.sat_downlink_high = (mySats.tpx_freq_list[mySats.current_sat][index_tpx])[3]
        
        if((mySats.tpx_freq_list[mySats.current_sat][index_tpx])[4] == "False"):
            # Non inverted tpx
            myFreqs.current_tpx_inversion = 0;
        else:
			# Inverted TPX
            myFreqs.current_tpx_inversion = 1;
            
        if myFreqs.sat_uplink_high == None and myFreqs.sat_uplink_low != None:
            # FM sat or simple up/down satellite
            mySats.sat_is_linear = 0;
            myFreqs.current_downlink =  myFreqs.sat_downlink_low;
            myFreqs.current_uplink = myFreqs.sat_uplink_low;
        elif myFreqs.sat_uplink_high == None and myFreqs.sat_uplink_low == None:
            # No uplink -> beacon / downlink only
            myFreqs.current_downlink =  myFreqs.sat_downlink_low;
            myFreqs.current_uplink = 0;
        
        else:
            # TPX sat
            mySats.sat_is_linear = 1;
            myFreqs.current_downlink = (myFreqs.sat_downlink_high + myFreqs.sat_downlink_low)/2;
            myFreqs.current_uplink = (myFreqs.sat_uplink_high + myFreqs.sat_uplink_low)/2;
            
        # Update GUI after new selection
        # manually triggerd here once just in case
        self.update_frequency_labels()
    
    
    ### update gui labels
    def update_frequency_labels(self):
        self.downlink_tpx.setText(str((myFreqs.current_downlink+ myFreqs.current_tpx_offset)/(1e6)) +" MHz")
        self.doppler_up.setText("%+.0f Hz" % myFreqs.doppler_up)
        self.doppler_down.setText("%+.0f Hz" % myFreqs.doppler_down)
        self.satellite_elevation.setText("%+.1f°" % mySats.current_sat_ele)
        self.satellite_azimuth.setText("%+.1f°" % mySats.current_sat_azi)
        self.freqRIT.setText("%+.0f Hz" % myFreqs.rit)

        self.freqDOWN_LCD.display("%.6f" % (((myFreqs.current_downlink + myFreqs.doppler_down)/(1e6)) + myFreqs.current_tpx_offset/(1e6)))
        if(myFreqs.current_tpx_inversion == 1):
            self.freqUP_LCD.display("%.6f" % (((myFreqs.current_uplink + myFreqs.doppler_up)/(1e6))- myFreqs.current_tpx_offset/(1e6)))
            self.uplink_tpx.setText(str((myFreqs.current_uplink- myFreqs.current_tpx_offset)/(1e6)) + " MHz")
        else:
            self.freqUP_LCD.display("%.6f" % (((myFreqs.current_uplink + myFreqs.doppler_up)/(1e6))+ myFreqs.current_tpx_offset/(1e6)))
            self.uplink_tpx.setText(str((myFreqs.current_uplink+ myFreqs.current_tpx_offset)/(1e6)) + " MHz")
			
    ### Calculate doppler values and sat position
    def update_doppler(self):
		#Update time and pos
        mySats.tracker_list[mySats.current_sat].set_epoch(time.time())
        mySats.current_sat_azi = mySats.tracker_list[mySats.current_sat].azimuth()
        mySats.current_sat_ele = mySats.tracker_list[mySats.current_sat].elevation()
        
        #Update frequencies
        myFreqs.doppler_up = -mySats.tracker_list[mySats.current_sat].doppler(myFreqs.current_uplink)
        myFreqs.doppler_down = mySats.tracker_list[mySats.current_sat].doppler(myFreqs.current_downlink)
        self.update_frequency_labels()
        self.update_rig_frequencies()

    ### TPX offset from slider 
    def update_tpx_offset(self, offset):
        myFreqs.current_tpx_offset = offset
    ### Reset TPX offset    
    def reset_tpx_offset(self):
        myFreqs.current_tpx_offset = 0
        self.slider_tpx.setValue(0)
    
    
    ### Updates Rig frequencies and recalculaltes tune frequency for sdr  
    # frequency sanity check todo  
    def update_rig_frequencies(self):
        
        
        
        myFreqs.current_rig_downlink = (((myFreqs.current_downlink + myFreqs.doppler_down)/(1e6)) + myFreqs.current_tpx_offset/(1e6))
        if(myFreqs.current_tpx_inversion == 1):
            myFreqs.current_rig_uplink = (((myFreqs.current_uplink + myFreqs.doppler_up)/(1e6))- myFreqs.current_tpx_offset/(1e6))
        else:
            myFreqs.current_rig_uplink = (((myFreqs.current_uplink + myFreqs.doppler_up)/(1e6))+ myFreqs.current_tpx_offset/(1e6))
        
        
        
        if (myRig.rig_uplink_connected== 1):
            myRig.trigger_uplink_changed = 1
            #tx_rig.set_freq(Hamlib.RIG_VFO_A,float(int(myFreqs.current_rig_uplink*1e6)))
            
        if(demod_chain == "0"):
            if (myFreqs.current_downlink < 28000000):
                myFreqs.current_downlink = 28000000
            # if rx is more than 1 MHz rom tune frequency, retune 500 kHz below rx
            if(abs(myFreqs.current_downlink-myFreqs.sdr_pll_freq) > 100000.0):
                myFreqs.sdr_pll_freq = myFreqs.current_downlink - 50000
                cmd = struct.pack(">BI", 1,  int(myFreqs.sdr_pll_freq))
                nmux_proc.write(cmd)
            
            myFreqs.sdr_shift = float(myFreqs.sdr_pll_freq - myFreqs.current_downlink)/myFreqs.sdr_samplerate
            os.write(csdr_shift_fifo_file,str.encode("%+.6f\n" % myFreqs.sdr_shift))
        elif(demod_chain == "1"):
            try:
                s.connect(("127.0.0.1", 6020))
                update_freq_simple_demod(myFreqs.current_rig_downlink)
            except:
                pass


    def update_tab_widget(self,index):
        if(index == 1):
            self.plot_graph()
            

		  
    def __init__(self):
        super(Ui, self).__init__()
        
        ### Enable to remove frame
        #self.setWindowFlag(Qt.FramelessWindowHint)
        
        self.get_satellites()
        self.get_modes()
              
        uic.loadUi('AmsatGUI-small.ui', self)
        self.label_DOWNLINK.setStyleSheet(''' font-size: 24px; ''')
        self.label_UPLINK.setStyleSheet(''' font-size: 24px; ''')
        self.label_RIT.setStyleSheet(''' font-size: 18px; ''')
        self.freqRIT.setStyleSheet(''' font-size: 18px; ''')
        self.label_tpx_lower_limit.setStyleSheet(''' font-size: 12px; ''')
        self.label_tpx_upper_limit.setStyleSheet(''' font-size: 12px; ''')
        self.label_tpx_mid.setStyleSheet(''' font-size: 12px; ''')
    
        self.label_uplink_tpx.setStyleSheet(''' font-size: 20px; ''')
        self.label_downlink_tpx.setStyleSheet(''' font-size: 20px; ''')
        self.label_doppler_up.setStyleSheet(''' font-size: 20px; ''')
        self.label_doppler_down.setStyleSheet(''' font-size: 20px; ''')
    
        self.uplink_tpx.setStyleSheet(''' font-size: 20px; ''')
        self.downlink_tpx.setStyleSheet(''' font-size: 20px; ''')
        self.doppler_up.setStyleSheet(''' font-size: 20px; ''')
        self.doppler_down.setStyleSheet(''' font-size: 20px; ''')
    
        self.label_satellite.setStyleSheet(''' font-size: 22px; ''')
        self.label_mode.setStyleSheet(''' font-size: 22px; ''')
        self.label_demod.setStyleSheet(''' font-size: 22px; ''')
    
        self.satellite_elevation.setStyleSheet(''' font-size: 22px; ''')
        self.satellite_azimuth.setStyleSheet(''' font-size: 22px; ''')
        self.label_azimuth.setStyleSheet(''' font-size: 22px; ''')
        self.label_elevation.setStyleSheet(''' font-size: 22px; ''')
        

        for d in demod_list:
            self.demod_selector.addItem(d)
        
        self.line_sep_1.setStyleSheet("background-color: #ff1744;");
        self.line_sep_2.setStyleSheet("background-color: #ff1744;");
        self.line_sep_3.setStyleSheet("background-color: #ff1744;");
        self.line_sep_4.setStyleSheet("background-color: #ff1744;");
        self.line_sep_5.setStyleSheet("background-color: #ff1744;");
        
        self.pass_chart = plt.figure()
        self.pass_chart.tight_layout()
        
        self.polarplot_widget = FigureCanvasQTAgg(self.pass_chart)
        self.polarplot_widget.setParent(self.current_pass_tab)
        self.pushButton.clicked.connect(self.plot_graph)
        self.tabWidget.setCurrentIndex(0) 
          
        self.comboboxSAT_set_entries()
        self.comboboxMode_set_entries(0)
        self.update_selected_tpx(0)
        self.update_frequencies(0)
        
        self.satellite_selector.currentIndexChanged.connect(self.comboboxMode_set_entries)
        self.mode_selector.currentIndexChanged.connect(self.update_selected_tpx)
        self.slider_tpx.valueChanged.connect(self.update_tpx_offset)
        self.demod_selector.currentIndexChanged.connect(self.update_selected_demod)
        self.tabWidget.currentChanged.connect(self.update_tab_widget)
                
        if (is_rpi == 1):
            enc_vfo = Encoder(16, 20, 21, self.valueChanged_VFO)
            enc_rit = Encoder(26, 19, 13, self.valueChanged_RIT)
        
        doppler_timer = QTimer(self)
        doppler_timer.timeout.connect(self.update_doppler)
        doppler_timer.start(500)
        
        sdr_rx_worker = sdr_rx()
        sdr_rx_thread = QThread(parent=self)
        sdr_rx_worker.moveToThread(sdr_rx_thread)

        sdr_rx_thread.started.connect(sdr_rx_worker.run)
        sdr_rx_thread.start()        
    
        uplink_tx_worker = uplink_tx()
        uplink_thread = QThread(parent=self)
        uplink_tx_worker.moveToThread(uplink_thread)
        
        uplink_thread.started.connect(uplink_tx_worker.run)
        uplink_thread.start()     
             
        self.show()
        apply_stylesheet(app, theme='dark_red.xml')
        
    def plot_graph(self):
        
        azimuth, elevation = mySats.tracker_list[mySats.current_sat].next_pass_table(30)
        azimuth = [radians(i) for i in azimuth]
        self.pass_chart.clear() 
        ax = self.pass_chart.subplots(subplot_kw={'projection': 'polar'})
        ax.plot(azimuth, elevation)
        ax.grid(True)
        ax.set_theta_direction(-1)
        ax.set_theta_offset(np.pi/2)
        ax.set_rlim(bottom=90, top=0)
        self.pass_chart.tight_layout()
        self.polarplot_widget.draw()
        self.polarplot_widget.setMaximumHeight(self.current_pass_tab.height())
        self.polarplot_widget.setMaximumWidth(int(self.current_pass_tab.width()/2))
        self.polarplot_widget.setMinimumHeight(self.current_pass_tab.height())
        self.polarplot_widget.setMinimumWidth(int(self.current_pass_tab.width()/2))
		

def application_exit_handler():
    os.kill(rtl_tcp_proc.processId(), signal.SIGKILL)
    os.kill(demod_proc.processId(), signal.SIGKILL)
    os.kill(nmux_proc.processId(), signal.SIGKILL)
    os.kill(sdr_demod_simple_proc.processId(), signal.SIGKILL)
    os.kill(sdr_hybrid_iq_proc.processId(), signal.SIGKILL)
    os.kill(sdr_hybrid_demod_proc.processId(), signal.SIGKILL)
    

app = QtWidgets.QApplication(sys.argv)
app.aboutToQuit.connect(application_exit_handler)
window = Ui()
app.exec()



