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

     
         
# Info    
rename the indexv[version].html to index.html in templates folder to make it work, the python file name doesn't matter.
           
check if you can ssh to fw and sensor using user root (password is also root)     
