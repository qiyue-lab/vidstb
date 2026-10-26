package com.xlabs.vidstb;

import android.content.Context;

import com.arthenica.ffmpegkit.FFmpegKitConfig;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;

public final class SubtitleFonts {
    private SubtitleFonts() {
    }

    public static void configure(Context context) throws IOException {
        File directory = new File(context.getFilesDir(), "fontconfig");
        File cacheDirectory = new File(context.getCacheDir(), "fontconfig");
        if ((!directory.isDirectory() && !directory.mkdirs())
                || (!cacheDirectory.isDirectory() && !cacheDirectory.mkdirs())) {
            throw new IOException("无法创建字体配置目录");
        }
        File config = new File(directory, "fonts.conf");
        String xml = "<?xml version=\"1.0\"?>\n"
                + "<!DOCTYPE fontconfig SYSTEM \"fonts.dtd\">\n"
                + "<fontconfig>\n"
                + "  <dir>/system/fonts</dir>\n"
                + "  <dir>/vendor/fonts</dir>\n"
                + "  <dir>/product/fonts</dir>\n"
                + "  <cachedir>" + escapeXml(cacheDirectory.getAbsolutePath()) + "</cachedir>\n"
                + "  <alias><family>Arial</family><prefer><family>sans-serif</family></prefer></alias>\n"
                + "  <alias><family>sans-serif</family><prefer><family>Noto Sans CJK SC</family>"
                + "<family>Roboto</family></prefer></alias>\n"
                + "</fontconfig>\n";
        try (FileOutputStream output = new FileOutputStream(config, false)) {
            output.write(xml.getBytes(StandardCharsets.UTF_8));
        }
        FFmpegKitConfig.setEnvironmentVariable("FONTCONFIG_FILE", config.getAbsolutePath());
        FFmpegKitConfig.setEnvironmentVariable("FONTCONFIG_PATH", directory.getAbsolutePath());
    }

    private static String escapeXml(String value) {
        return value.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace("\"", "&quot;");
    }
}
