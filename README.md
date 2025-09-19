# status_dashboard
Status dashboard for logsources health overview. Meant as intern project

Usage:     

First setup docker containers as fictional firewalls and sensors:  
./setup_demo.sh    

Then start dashboard using Python Flask:     
python dashboardv1.py      


Log in with: 
Username:    
soc_analyst    
Password:     
securepassword123     


Update to V2:    
Added Graph, added function to schedule update, added fix common usage  
Usage:   
python app.py    

     

# See P1 for first usable production version
Update:      
Added graph to a save file,    
Added scheduled tasks to a save file,    
Added config to a single config file in json format,     
Scheduled task create ticket      
All working buttons     

# Working version, Version1 first acceptable PoC version     
Usage:    
```
./setup-demov2.sh    
pyhon app.py     
```    
log into http://127.0.0.1:5000/ as soc_analyst or soc_manager     

         
# Info    
rename the indexv[version].html to index.html in templates folder to make it work, the python file name doesn't matter.
           
check if you can ssh to fw and sensor using user root (password is also root)     

Activate virtual environment first:   
```    
source venv/bin/activate     
```     
To install modules for python use:        
```    
pip install -r requirements.txt       
```
