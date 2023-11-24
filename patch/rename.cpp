/*
The process of receiving files from mobile QQ to Linux QQ is as follows:

1. Download the temporary file to the host machine's directory:
~/.var/app/com.qq.QQ/config/QQ/nt_qq_xxxxxxxxxxxxxxxxxxx/nt_data/dataline/.tmp/

2. Call the rename() function from glibc to move the temporary file to the host machine's ~/Download folder 
(or any other folder of your choice).

However, within the Flatpak container, the host machine's download directory and QQ's temporary file directory
are not on the same mount point. The glibc rename() function does not support moving files between different 
mount points because its implementation relies on hard link. Therefore, we need to rewrite a rename() function, 
compile it as a dynamic library, and inject it into QQ using LD_PRELOAD.

*/
#define _GNU_SOURCE
#include <iostream>
#include <dlfcn.h>
#include <errno.h>
#include <filesystem>

namespace fs = std::filesystem;

static int (*original_rename)(const char*, const char*) = 
    (int(*)(const char*, const char*))dlsym(RTLD_NEXT, "rename");

extern "C" int rename(const char* from, const char* to) {

    if (!original_rename) {
        std::cerr << "Error: dlsym couldn't find original rename function." << std::endl;
        return -1;
    }

    int result = original_rename(from, to);

    if (result == -1 && errno == EXDEV) {

        fs::path from_path(from);
        fs::path to_path(to);

        if(!std::filesystem::exists(from_path) && 
            std::filesystem::exists(to_path) ) {
            return 1;                                         // if qq call rename() fails, it tends to call mutiple
        }                                                    // times, so if the file is already copied just return.

        if (fs::is_directory(from_path)) {
            return result;                                    // we don't handle the situation where from is dir
        }
        
        std::error_code ec;
        fs::copy_options options = fs::copy_options::update_existing;

        fs::copy(from_path, to_path, options, ec);


        if (ec) {
            std::cerr << "ERCF:" << ec.message() << std::endl; // ERCF means error copy file
            return -1; 
        }

        fs::remove(from_path, ec);

        if (ec) {
            std::cerr << "ERRF:" << ec.message() << std::endl; // EROF means error remove file
            return -1; // Return an error code for old file removal failure.
        }

    }
    return result;

}
