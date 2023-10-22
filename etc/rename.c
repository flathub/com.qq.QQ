#define _GNU_SOURCE
#include <stdio.h>
#include <errno.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <dlfcn.h>

typedef int (*rename_func_type)(const char *oldpath, const char *newpath);

int rename(const char *oldpath, const char *newpath) {

    rename_func_type original_rename = dlsym(RTLD_NEXT, "rename");
    int status = original_rename(oldpath, newpath);
    if(status == 0) return 0;

    if (errno == EXDEV) {
        int fd_old = open(oldpath, O_RDONLY);
        if (fd_old == -1) {
            return -1; 
        }

        struct stat old_stat;
        if (fstat(fd_old, &old_stat) == -1) {
            close(fd_old);
            return -1; 
        }

        int fd_new = open(newpath, O_CREAT | O_WRONLY, old_stat.st_mode);
        if (fd_new == -1) {
            close(fd_old);
            return -1; 
        }

        char buf[4096];
        ssize_t n;

        while ((n = read(fd_old, buf, sizeof(buf))) > 0) {
            if (write(fd_new, buf, n) != n) {
                close(fd_old);
                close(fd_new);
                return -1;
            }
        }
        
        if (n == -1) {
            return -1;
        }

        close(fd_old);
        close(fd_new);

        if (unlink(oldpath) == -1) {
            return -1; 
        }

        return 0; 
    }
    return status;
}

__attribute__((constructor))
void preload_rename() {
    dlsym(RTLD_NEXT, "rename");
}
