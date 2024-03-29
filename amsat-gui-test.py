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
import signal

# Import external functions
from sdr_control import *
from freq_manager import *
from rig_manager import *
from sat_manager import *

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

# import dependencies for pass plotter
import matplotlib
matplotlib.use('Qt5Agg')
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
from matplotlib.figure import Figure
import matplotlib.pyplot as plt
import numpy as np
from math import radians



### Catalog with Satellite Data
mySats = SatCatalog()

### frequency management  
myFreqs = FreqManager()

### rig manager
myRig = RigManager()


# Communication with Rig
if (config_file['RIG_UPLINK']['enable_rig_uplink'] == "1" or config_file['RIG_DOWNLINK']['enable_rig_downlink'] == "1"):
    import serial
    import Hamlib

if (config_file['RIG_UPLINK']['enable_rig_uplink'] == "1"):
    global tx_rig
    if (config_file['RIG_UPLINK']['debug'] == "0"):
        Hamlib.rig_set_debug(Hamlib.RIG_DEBUG_NONE)
    else:
        Hamlib.rig_set_debug(Hamlib.RIG_DEBUG_VERBOSE)
        
    tx_rig = Hamlib.Rig(int(config_file['RIG_UPLINK']['rig_model']))
    tx_rig.set_conf("rig_pathname", config_file['RIG_UPLINK']['rig_port'])
    tx_rig.set_conf("retry", "5")
    try:
        tx_rig.open()
        myRig.rig_uplink_connected = 1
    except:
        myrig.rig_uplink_connected = 0

if (config_file['RIG_DOWNLINK']['enable_rig_downlink'] == "1"):
    global rx_rig
    if (config_file['RIG_DOWNLINK']['debug'] == "0"):
        Hamlib.rig_set_debug(Hamlib.RIG_DEBUG_NONE)
    else:
        Hamlib.rig_set_debug(Hamlib.RIG_DEBUG_VERBOSE)
        
    rx_rig = Hamlib.Rig(int(config_file['RIG_DOWNLINK']['rig_model']))
    rx_rig.set_conf("rig_pathname", config_file['RIG_DOWNLINK']['rig_port'])
    rx_rig.set_conf("retry", "5")
    try:
        rx_rig.open()
        myRig.rig_downlink_connected = 1
    except:
        myrig.rig_downlink_connected = 0
  
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
csdr_shift_fifo_path =  config_file['SDR']['demod_freq_shift_pipe'];
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

if(demod_chain != "-1"):
    global s
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    nmux_proc = QProcess()
    demod_proc = QProcess()
    rtl_tcp_proc = QProcess()
    sdr_demod_simple_proc = QProcess()
    sdr_hybrid_iq_proc = QProcess()
    sdr_hybrid_demod_proc = QProcess()

# split TLE into triple lists
def triple(l, n):
  return [l[i:i+n] for i in range(0, len(l), n)]

### TLE file 
tle_file = open(config_file['SAT_DATA']['tle_path'], 'r')
if tle_file == None:
    print("TLE file missing")
    exit()
tle_triple = triple(tle_file.readlines(), 3)

### TPX file from satnogs
tpx_file = open(config_file['SAT_DATA']['db_path'], 'r')
if tpx_file == None:
    print("TPX file missing")
    exit()
tpx_data = json.loads(tpx_file.read())


class uplink_tx(QObject):
    def __init__(self):
        super().__init__()
        print("Uplink TX Init")
    def run(self):
        while 1:
            diff = abs(int(myFreqs.current_rig_uplink*1e6)-int(myFreqs.old_rig_uplink*1e6))
            if myRig.trigger_uplink_changed == 1 and diff >= int(config_file['RIG_UPLINK']['update_diff']) and int(myFreqs.current_rig_uplink) > 1:
                myFreqs.old_rig_uplink = myFreqs.current_rig_uplink
                tx_rig.set_freq(Hamlib.RIG_VFO_A,float(int(myFreqs.current_rig_uplink*1e6)))
                myRig.trigger_uplink_changed = 0
            time.sleep(int(config_file['RIG_UPLINK']['update_rate'])/1000)
                        
class downlink_rx(QObject):
    def __init__(self):
        super().__init__()
        print("Downlink RX Init")
    def run(self):
        while 1:
            diff = abs(int(myFreqs.current_rig_downlink*1e6)-int(myFreqs.old_rig_downlink*1e6))
            if myRig.trigger_uplink_changed == 1 and diff >= int(config_file['RIG_DOWNLINK']['update_diff']) and int(myFreqs.current_rig_downlink) > 1:
                myFreqs.old_rig_downlink = myFreqs.current_rig_downlink
                rx_rig.set_freq(Hamlib.RIG_VFO_A,float(int(myFreqs.current_rig_downlink*1e6)))
                myRig.trigger_downlink_changed = 0
            time.sleep(int(config_file['RIG_UPLINK']['update_rate'])/1000)                        
                
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
        self.update_doppler()
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
        if (myRig.rig_downlink_connected== 1):
            myRig.trigger_downlink_changed = 1    
            
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
        elif(demod_chain == "1" or demod_chain == "2"):
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
        
        
        if (config_file['SDR']['demod_chain'] != "-1"):
            sdr_rx_worker = sdr_rx()
            sdr_rx_thread = QThread(parent=self)
            sdr_rx_worker.moveToThread(sdr_rx_thread)
            sdr_rx_thread.started.connect(sdr_rx_worker.run)
            sdr_rx_thread.start()        
    
        if (config_file['RIG_UPLINK']['enable_rig_uplink'] == "1"):
            uplink_tx_worker = uplink_tx()
            uplink_thread = QThread(parent=self)
            uplink_tx_worker.moveToThread(uplink_thread)
            uplink_thread.started.connect(uplink_tx_worker.run)
            uplink_thread.start()
        if (config_file['RIG_DOWNLINK']['enable_rig_downlink'] == "1"):
            downlink_rx_worker = downlink_rx()
            downlink_thread = QThread(parent=self)
            downlink_rx_worker.moveToThread(downlink_thread)
            downlink_thread.started.connect(downlink_rx_worker.run)
            downlink_thread.start()  
             
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
    if (config_file['SDR']['demod_chain'] != "-1"):
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



