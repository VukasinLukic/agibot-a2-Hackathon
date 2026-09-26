/* If the VirusTotal check is a bust (<90% consensus by the providers that the file is safe)
   Then we must take things into our own hands.
   
   We will run a seperate VM in the robot, and execute the file, checking to see what specifically
   that file does to the system, if anything.

   We will also run other checks of potential code or commands that could be in the JS of the PDF file.
   
   If something is detected, the system will delete the file in the main system folder,
   and report the issue to the company IT/Cybersecurity division, citing that the file
   needs to be checked, and that it could be potentially unsafe. 

   The vendor/uploader will be notified that the file failed the safety check, and that
   the company's IT department will look into it, and leave an email for them to contact them 
   from.
 */

#include <iostream>
#include <stdlib.h>

bool load_VM(){
    // Load VM and return True if loaded or False if not.
    // If x, return error message.
    
    
}

bool check_virus(char FILE_PATH){

}

