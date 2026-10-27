package com.xlabs.vidstb;

import androidx.lifecycle.ViewModel;

public final class EditorViewModel extends ViewModel {
    private final EditProject project = new EditProject();
    private int selectedIndex = -1;
    private long nextId = 1L;
    private String workDirectory = "";

    public EditProject getProject() { return project; }
    public int getSelectedIndex() { return selectedIndex; }
    public void setSelectedIndex(int index) { selectedIndex = index; }
    public long nextId() { return nextId++; }
    public String getWorkDirectory() { return workDirectory; }
    public void setWorkDirectory(String path) { workDirectory = path; }
}
