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

#include <iostream>
#include <dlfcn.h>
#include <errno.h>
#include <filesystem>

namespace fs = std::filesystem;

static int (*original_rename)(const char*, const char*) = 
    (int(*)(const char*, const char*))dlsym(RTLD_NEXT, "rename");

extern "C" int rename(const char* oldpath, const char* newpath) {

    if (!original_rename) {
        std::cerr << "Error: dlsym couldn't find original rename function." << std::endl;
        return -1; // 返回错误码
    }

    int result = original_rename(oldpath, newpath);

    if (result == -1 && errno == EXDEV) {

        fs::path old_path(oldpath);
        if (fs::is_directory(old_path)) {
            return 1; // 如果是文件夹，返回1
        }

        try {
            fs::copy(old_path, newpath, fs::copy_options::update_existing);
            fs::remove(old_path);
            return 0; // 返回0表示成功
        } catch (const std::exception& e) {
            std::cerr << "Error copying file: " << e.what() << std::endl;
            return -1; // 返回错误码
        }

    }
    return result;

}
