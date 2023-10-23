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
#include <dlfcn.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/sendfile.h>
#include <unistd.h>

int (*real_rename)(const char *oldpath, const char *newpath) = NULL;

int rename(const char *oldpath, const char *newpath) {
    if (real_rename == NULL) {
        real_rename = dlsym(RTLD_NEXT, "rename");
    }

    int result = real_rename(oldpath, newpath);
    if (result == -1 && errno == EXDEV) {
        struct stat old_stat;
        if (stat(oldpath, &old_stat) == 0 && S_ISDIR(old_stat.st_mode)) {
            return 1; // We don't care about the diretory, we only fix what we need to fix.
        }

        int old_fd = open(oldpath, O_RDONLY);
        if (old_fd == -1) {
            return -1;
        }

        int new_fd = open(newpath, O_CREAT | O_WRONLY, old_stat.st_mode);
        if (new_fd == -1) {
            close(old_fd);
            return -1;
        }

        off_t offset = 0;
        int ret = sendfile(new_fd, old_fd, &offset, old_stat.st_size);
        if (ret == -1) {
            close(old_fd);
            close(new_fd);
            return -1;
        }

        close(old_fd);
        close(new_fd);

        if (unlink(oldpath) == -1) {
            return -1;
        }
    }
    return result;
}

__attribute__((constructor))
void preload_rename() {
    dlsym(RTLD_NEXT, "rename");
}