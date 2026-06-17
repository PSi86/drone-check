drone-check

Outline: Document the firmware version and all settings of a drone.

Details:
- when a drone is detected, the user should be asked for the pilot name. (optionally)
- support for Betaflight, INAV and later optionally Kiss Ultra
- drone is connected via usb serial, commanded into cli mode and instructed to output fw information (hash) and settings. check received data for completeness on each step. after all steps exit the drone flight-controller cleanly. Then wait for serial disconnect and connection of a new drone.
- log all data cleanly into folders that are named after the pilot name from the firmware data and by drone (flight controller serial)
- run configurable checks on the data of each drone: FW Hash check against a list or online sources (directly from official fimware github?), configured power levels for vtx (armed and disarmed) as well as user switches controlling the vtx power level. There needs to be a (not self-invended) syntax to express those rules. 
- evaluate of the drone fulfills all requirements and show green or red while documenting and showing the reasons for the evaluation result.
