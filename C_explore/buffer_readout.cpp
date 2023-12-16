#include <c:/Program Files/IVI Foundation/VISA/Win64/Include/visa.h>
#include <iostream>
#include <vector>
#include <string>



#include <iostream>
#include <string>
#include <visa.h> // Make sure to include the VISA library

void readBufferFromSR830(const char* gpibAddress) {
    ViSession defaultRM, instrument;
    ViStatus status;
    ViUInt32 bytesRead;
    ViChar buffer[100]; // Adjust the buffer size as per your requirement

    // Open a session to the default resource manager
    status = viOpenDefaultRM(&defaultRM);
    if (status < VI_SUCCESS) {
        std::cout << "Failed to open default resource manager." << std::endl;
        return;
    }

    // Open a session to the instrument
    status = viOpen(defaultRM, gpibAddress, VI_NULL, VI_NULL, &instrument);
    if (status < VI_SUCCESS) {
        std::cout << "Failed to open instrument." << std::endl;
        viClose(defaultRM);
        return;
    }

    // Send command to request data
    status = viWrite(instrument, (ViBuf)"SNAP?1,2", 8, &bytesRead);
    if (status < VI_SUCCESS) {
        std::cout << "Failed to write command." << std::endl;
        viClose(instrument);
        viClose(defaultRM);
        return;
    }

    // Read response from the instrument
    status = viRead(instrument, (ViBuf)buffer, 100, &bytesRead);
    if (status < VI_SUCCESS) {
        std::cout << "Failed to read data." << std::endl;
    } else {
        std::string data(buffer);
        std::cout << "Received data: " << data << std::endl;
        // Process or parse the received data as needed
    }

    // Close the instrument session and the resource manager session
    viClose(instrument);
    viClose(defaultRM);
}

int main() {
    const char* gpibAddress = "GPIB0::8::INSTR"; // Replace this with the appropriate GPIB address
    readBufferFromSR830(gpibAddress);

    return 0;
}


// using namespace std;
// // int main() {

// //     return 0;
// // }

// int main()
// {
//     vector<string> msg {"Hello", "World", "Lmao"};

//     for (const string& word : msg)
//     {
//         cout << word << " ";
//     }

//     cout << endl;
// }
